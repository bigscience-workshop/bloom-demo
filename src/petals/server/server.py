from __future__ import annotations

import gc
import multiprocessing as mp
import random
import threading
import time
from typing import Dict, List, Optional, Union

import numpy as np
import psutil
import torch
from hivemind import DHT, MAX_DHT_TIME_DISCREPANCY_SECONDS, BatchTensorDescriptor, get_dht_time
from hivemind.moe.server.layers import add_custom_models_from_file
from hivemind.moe.server.runtime import Runtime
from hivemind.proto.runtime_pb2 import CompressionType
from hivemind.utils.logging import get_logger, use_hivemind_log_handler

from petals.bloom.from_pretrained import DTYPE_MAP, load_pretrained_block
from petals.bloom.model import BloomConfig
from petals.constants import PUBLIC_INITIAL_PEERS
from petals.data_structures import CHAIN_DELIMITER, UID_DELIMITER, ServerState
from petals.dht_utils import declare_active_modules, get_remote_module_infos
from petals.server import block_selection
from petals.server.backend import TransformerBackend
from petals.server.cache import MemoryCache
from petals.server.handler import TransformerConnectionHandler
from petals.server.throughput import get_host_throughput
from petals.utils.convert_8bit import replace_8bit_linear

use_hivemind_log_handler("in_root_logger")
logger = get_logger(__file__)


class Server:
    """
    Runs ModuleContainer, periodically checks that the network is balanced,
    restarts the ModuleContainer with other layers if the imbalance is significant
    """

    def __init__(
        self,
        *,
        initial_peers: List[str],
        prefix: Optional[str],
        converted_model_name_or_path: str,
        throughput: Union[float, str],
        num_blocks: Optional[int] = None,
        block_indices: Optional[str] = None,
        num_handlers: int = 8,
        min_batch_size: int = 1,
        max_batch_size: int = 2048,
        inference_max_length: int = 2048,
        torch_dtype: str = "auto",
        revision: str = "main",
        cache_dir: Optional[str] = None,
        attn_cache_size: Optional[int] = None,
        alloc_timeout: float = 60,
        device: Optional[Union[str, torch.device]] = None,
        compression=CompressionType.NONE,
        stats_report_interval: Optional[int] = None,
        custom_module_path=None,
        update_period: float = 30,
        expiration: Optional[float] = None,
        request_timeout: float = 3 * 60,
        session_timeout: float = 30 * 60,
        step_timeout: float = 5 * 60,
        prefetch_batches: int = 1,
        sender_threads: int = 1,
        balance_quality: float = 0.75,
        mean_balance_check_period: float = 60,
        mean_block_selection_delay: float = 0.5,
        use_auth_token: Optional[str] = None,
        load_in_8bit: bool = False,
        **kwargs,
    ):
        """Create a server with one or more bloom blocks. See run_server.py for documentation."""

        self.converted_model_name_or_path = converted_model_name_or_path
        self.num_handlers = num_handlers
        self.min_batch_size, self.max_batch_size = min_batch_size, max_batch_size
        self.inference_max_length = inference_max_length
        self.cache_dir = cache_dir
        self.attn_cache_size = attn_cache_size
        self.compression = compression
        self.stats_report_interval, self.update_period = stats_report_interval, update_period
        self.prefetch_batches, self.sender_threads = prefetch_batches, sender_threads
        self.use_auth_token = use_auth_token
        self.load_in_8bit = load_in_8bit

        if custom_module_path is not None:
            add_custom_models_from_file(custom_module_path)

        if prefix is None:
            prefix = converted_model_name_or_path
            assert UID_DELIMITER not in prefix and CHAIN_DELIMITER not in prefix, (
                f"Cannot use model name as prefix (contains '{UID_DELIMITER}' or '{CHAIN_DELIMITER}'); "
                f"Please specify --prefix manually when starting a server"
            )
            logger.info(f"Automatic dht prefix: {prefix}")
        self.prefix = prefix

        if expiration is None:
            expiration = max(2 * update_period, MAX_DHT_TIME_DISCREPANCY_SECONDS)
        self.expiration = expiration

        self.request_timeout = request_timeout
        self.session_timeout, self.step_timeout = session_timeout, step_timeout

        self.dht = DHT(initial_peers=initial_peers, start=True, **kwargs)
        visible_maddrs_str = [str(a) for a in self.dht.get_visible_maddrs()]
        if initial_peers == PUBLIC_INITIAL_PEERS:
            logger.info("Connecting to the public Petals swarm")
        else:
            logger.info(f"Running DHT node on {visible_maddrs_str}, initial peers = {initial_peers}")

        device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.device = device

        self.memory_cache = MemoryCache(device, attn_cache_size, alloc_timeout)

        assert isinstance(throughput, float) or throughput in ["auto", "eval"]
        if throughput in ["auto", "eval"]:
            throughput = get_host_throughput(device, force_eval=(throughput == "eval"))
        self.throughput = throughput

        if isinstance(torch_dtype, str):
            torch_dtype = DTYPE_MAP[torch_dtype]
        assert torch_dtype in DTYPE_MAP.values(), f"torch_dtype must be one of {list(DTYPE_MAP.values())}"
        self.torch_dtype = torch_dtype

        self.block_config = BloomConfig.from_pretrained(
            converted_model_name_or_path,
            use_auth_token=use_auth_token,
            revision=revision,
        )
        self.module_uids = [f"{self.prefix}.{block_index}" for block_index in range(self.block_config.n_layer)]

        assert (block_indices is None) != (num_blocks is None), "please specify num_blocks or block_indices, not both"
        if block_indices is not None:
            try:
                first_block_index, last_block_index = block_indices.split(":")
                first_block_index, last_block_index = map(int, map(str.strip, (first_block_index, last_block_index)))
            except Exception as e:
                logger.error(f"Failed to parse --block_indices ({e}), must be start:end (e.g. 0:18)")
                raise
            block_indices = range(first_block_index, last_block_index)
        self.strict_block_indices, self.num_blocks = block_indices, num_blocks
        self.balance_quality = balance_quality
        self.mean_balance_check_period = mean_balance_check_period
        self.mean_block_selection_delay = mean_block_selection_delay

        self.stop = threading.Event()

    def run(self):
        while True:
            block_indices = self._choose_blocks()
            self.module_container = ModuleContainer.create(
                dht=self.dht,
                prefix=self.prefix,
                converted_model_name_or_path=self.converted_model_name_or_path,
                block_config=self.block_config,
                memory_cache=self.memory_cache,
                throughput=self.throughput,
                block_indices=block_indices,
                num_handlers=self.num_handlers,
                min_batch_size=self.min_batch_size,
                max_batch_size=self.max_batch_size,
                inference_max_length=self.inference_max_length,
                torch_dtype=self.torch_dtype,
                cache_dir=self.cache_dir,
                device=self.device,
                compression=self.compression,
                stats_report_interval=self.stats_report_interval,
                update_period=self.update_period,
                expiration=self.expiration,
                request_timeout=self.request_timeout,
                session_timeout=self.session_timeout,
                step_timeout=self.step_timeout,
                prefetch_batches=self.prefetch_batches,
                sender_threads=self.sender_threads,
                use_auth_token=self.use_auth_token,
                load_in_8bit=self.load_in_8bit,
                start=True,
            )
            try:
                self.module_container.ready.wait()

                while True:
                    timeout = random.random() * 2 * self.mean_balance_check_period
                    # TODO: Follow ModuleContainer status (to restart/stop if it crashes)
                    if self.stop.wait(timeout):
                        return

                    if self._should_choose_other_blocks():
                        logger.info("Swarm is imbalanced, server will load other blocks")
                        break  # Stop serving this set of modules
            finally:
                self.module_container.shutdown()

            self._clean_memory_and_fds()

    def _clean_memory_and_fds(self):
        del self.module_container
        gc.collect()  # In particular, this closes unused file descriptors

        cur_proc = psutil.Process()
        num_fds = [proc.num_fds() for proc in [cur_proc] + psutil.Process().children(recursive=True)]
        logger.info(f"Cleanup complete, {sum(num_fds)} open file descriptors left")

    def _choose_blocks(self) -> List[int]:
        if self.strict_block_indices is not None:
            return self.strict_block_indices
        assert self.num_blocks is not None

        # If multiple servers (e.g., launched on the same machine by a script) get to this line at the same time,
        # this delay decreases the probability of a race condition while choosing the best blocks to serve.
        time.sleep(random.random() * 2 * self.mean_block_selection_delay)
        module_infos = get_remote_module_infos(self.dht, self.module_uids, expiration_time=np.inf)
        return block_selection.choose_best_blocks(self.num_blocks, module_infos)

    def _should_choose_other_blocks(self) -> bool:
        if self.strict_block_indices is not None:
            return False

        module_infos = get_remote_module_infos(self.dht, self.module_uids, expiration_time=np.inf)
        return block_selection.should_choose_other_blocks(self.dht.peer_id, module_infos, self.balance_quality)

    def shutdown(self):
        self.stop.set()

        self.dht.shutdown()
        self.dht.join()


class ModuleContainer(threading.Thread):
    """Serves a set of specific Bloom layers for inference, forward, and backward. Announces itself over the DHT."""

    # noinspection PyMethodOverriding
    @classmethod
    def create(
        cls,
        *,
        dht: DHT,
        prefix: str,
        converted_model_name_or_path: str,
        block_config: BloomConfig,
        memory_cache: MemoryCache,
        throughput: float,
        block_indices: List[int],
        min_batch_size: int,
        max_batch_size: int,
        torch_dtype: torch.dtype,
        cache_dir: Optional[str],
        device: Union[str, torch.device],
        compression: CompressionType,
        update_period: float,
        expiration: Optional[float],
        use_auth_token: Optional[str],
        load_in_8bit: bool,
        **kwargs,
    ) -> ModuleContainer:
        module_uids = [f"{prefix}.{block_index}" for block_index in block_indices]
        joining_announcer = ModuleAnnouncerThread(
            module_uids,
            dht,
            ServerState.JOINING,
            throughput=throughput,
            update_period=update_period,
            expiration=expiration,
            daemon=True,
        )
        joining_announcer.start()
        logger.info(f"Announced that blocks {block_indices} are joining")

        try:
            blocks = {}
            for module_uid, block_index in zip(module_uids, block_indices):
                block = load_pretrained_block(
                    converted_model_name_or_path,
                    block_index,
                    block_config,
                    torch_dtype=torch_dtype,
                    use_auth_token=use_auth_token,
                    cache_dir=cache_dir,
                )

                if load_in_8bit:
                    block = replace_8bit_linear(block)

                block = block.to(device)
                for param in block.parameters():
                    param.requires_grad = False

                backend_dtype = block.input_layernorm.weight.dtype if torch_dtype == "auto" else torch_dtype
                blocks[module_uid] = TransformerBackend(
                    module_uid,
                    block,
                    memory_cache=memory_cache,
                    backend_dtype=backend_dtype,
                    args_schema=(
                        BatchTensorDescriptor(
                            1, 2048, block_config.hidden_size, dtype=backend_dtype, compression=compression
                        ),
                    ),
                    kwargs_schema={},
                    outputs_schema=(
                        BatchTensorDescriptor(
                            1, 2048, block_config.hidden_size, dtype=backend_dtype, compression=compression
                        ),
                    ),
                    min_batch_size=min_batch_size,
                    max_batch_size=max_batch_size,
                )
        except:
            joining_announcer.stop.set()
            joining_announcer.join()
            declare_active_modules(
                dht,
                module_uids,
                expiration_time=get_dht_time() + expiration,
                state=ServerState.OFFLINE,
                throughput=throughput,
            )
            logger.info(f"Announced that blocks {module_uids} are offline")
            raise
        else:
            joining_announcer.stop.set()
            joining_announcer.join()

        return cls(
            dht,
            blocks,
            throughput=throughput,
            device=device,
            update_period=update_period,
            expiration=expiration,
            **kwargs,
        )

    def __init__(
        self,
        dht: DHT,
        module_backends: Dict[str, TransformerBackend],
        *,
        inference_max_length: int,
        num_handlers: int,
        throughput: float,
        update_period: float,
        expiration: Optional[float] = None,
        request_timeout: float,
        session_timeout: float,
        step_timeout: float,
        start: bool,
        **kwargs,
    ):
        super().__init__()

        self.dht, self.module_backends = dht, module_backends
        self.throughput, self.update_period, self.expiration = throughput, update_period, expiration
        self.conn_handlers = [
            TransformerConnectionHandler(
                dht,
                self.module_backends,
                inference_max_length=inference_max_length,
                request_timeout=request_timeout,
                session_timeout=session_timeout,
                step_timeout=step_timeout,
            )
            for _ in range(num_handlers)
        ]
        self.runtime = Runtime(self.module_backends, **kwargs)
        self.online_announcer = ModuleAnnouncerThread(
            list(self.module_backends.keys()),
            dht,
            ServerState.ONLINE,
            throughput=throughput,
            update_period=update_period,
            expiration=expiration,
            daemon=True,
        )
        self.checkpoint_saver = None  # no need to save checkpoints since we do not change model state

        if start:
            self.run_in_background(await_ready=True)

    def run(self):
        """
        Runs ModuleContainer in the current thread. Initializes dht if necessary, starts connection handlers,
        runs Runtime (self.runtime) to process incoming requests.
        """
        if not self.dht.is_alive():
            self.dht.run_in_background(await_ready=True)

        self.online_announcer.start()

        if self.checkpoint_saver is not None:
            self.checkpoint_saver.start()

        for handler in self.conn_handlers:
            handler.run_in_background()

        self.runtime.run()

    def run_in_background(self, await_ready=True, timeout=None):
        """
        Starts ModuleContainer in a background thread. if await_ready, this method will wait until the container
        is ready to process incoming requests or for :timeout: seconds max.
        """
        self.start()
        if await_ready and not self.ready.wait(timeout=timeout):
            raise TimeoutError("ModuleContainer didn't notify .ready in {timeout} seconds")

    @property
    def ready(self) -> mp.synchronize.Event:
        """
        An event (multiprocessing.Event) that is set when the container is ready to process requests.

        Example
        =======
        >>> container.start()
        >>> container.ready.wait(timeout=10)
        >>> print("Container ready" if container.ready.is_set() else "Container didn't start in 10 seconds")
        """
        return self.runtime.ready  # mp.Event that is true if self is ready to process batches

    def shutdown(self):
        """
        Gracefully terminate the container, process-safe.
        Please note that terminating container otherwise (e.g. by killing processes) may result in zombie processes.
        If you did already cause a zombie outbreak, your only option is to kill them with -9 (SIGKILL).
        """
        self.online_announcer.stop.set()
        self.online_announcer.join()

        declare_active_modules(
            self.dht,
            self.module_backends.keys(),
            expiration_time=get_dht_time() + self.expiration,
            state=ServerState.OFFLINE,
            throughput=self.throughput,
        )
        logger.info(f"Announced that blocks {list(self.module_backends.keys())} are offline")

        self.ready.clear()

        for handler in self.conn_handlers:
            handler.shutdown()
        logger.debug("Connection handlers terminated")

        if self.checkpoint_saver is not None:
            self.checkpoint_saver.stop.set()
            self.checkpoint_saver.join()

        logger.debug(f"Shutting down pools")
        for pool in self.runtime.pools:
            if pool.is_alive():
                pool.shutdown()

        logger.debug(f"Shutting down runtime")
        self.runtime.shutdown()

        logger.info("Module container shut down succesfully")


class ModuleAnnouncerThread(threading.Thread):
    """Periodically announces that this container hosts the specified modules, visible to all DHT peers"""

    def __init__(
        self,
        module_uids: List[str],
        dht: DHT,
        state: ServerState,
        *,
        throughput: float,
        update_period: float = 30,
        expiration: float,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.module_uids = module_uids
        self.dht = dht
        self.state = state
        self.throughput = throughput
        self.update_period = update_period
        self.expiration = expiration
        self.stop = threading.Event()

    def run(self) -> None:
        while True:
            declare_active_modules(
                self.dht,
                self.module_uids,
                expiration_time=get_dht_time() + self.expiration,
                state=self.state,
                throughput=self.throughput,
            )
            if self.stop.wait(self.update_period):
                break
