import contextlib
import dataclasses
import json
import os
import re
import tempfile
import threading
from typing import List, Optional, Tuple, Union

import torch
from hivemind.utils.logging import get_logger
from transformers import BloomPreTrainedModel, modeling_utils

from petals.client.routing.sequence_manager import SequenceManagerConfig

logger = get_logger(__name__)


@dataclasses.dataclass
class DistributedPretrainedConfig(SequenceManagerConfig):
    pre_seq_len: int = 0  # a number of tokens for prompt tuning.
    tuning_mode: Optional[str] = None  # fine-tuning regime, one of [None, "ptune", "deep_ptune"]

    # This settings matter for running the client with dtype bfloat16 on CPU.
    # If the CPU doesn't support AVX512, chunked_forward() significantly speeds up computations.
    use_chunked_forward: Union[str, bool] = "auto"
    chunked_forward_step: int = 16384


class FromPretrainedMixin:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: Union[str, os.PathLike, None],
        *args,
        low_cpu_mem_usage: Optional[bool] = None,
        torch_dtype: Optional[Union[str, torch.dtype]] = None,
        dht_prefix: Optional[str] = None,
        **kwargs,
    ):
        if low_cpu_mem_usage is None:
            low_cpu_mem_usage = True
        if torch_dtype is None:
            # torch_dtype=None gives torch.float32 in transformers>=4.26.0. In contrast,
            # torch_dtype="auto" attempts to (1) use config.torch_dtype (if exists), (2) use dtype of the weights.
            torch_dtype = "auto"
        if dht_prefix is None and pretrained_model_name_or_path is not None:
            is_local = os.path.isdir(pretrained_model_name_or_path)
            if not is_local:
                dht_prefix = str(pretrained_model_name_or_path)

        with ignore_keys(cls._keys_to_ignore_on_load_unexpected):
            return super().from_pretrained(
                pretrained_model_name_or_path,
                *args,
                low_cpu_mem_usage=low_cpu_mem_usage,
                torch_dtype=torch_dtype,
                dht_prefix=dht_prefix,
                **kwargs,
            )

    from_pretrained.__doc__ = BloomPreTrainedModel.from_pretrained.__doc__.replace(
        "low_cpu_mem_usage(`bool`, *optional*)",
        "low_cpu_mem_usage(`bool`, *optional*, defaults to `True` in Petals)",
    ).replace(
        "torch_dtype (`str` or `torch.dtype`, *optional*)",
        'torch_dtype (`str` or `torch.dtype`, *optional*, defaults to `"auto"` in Petals)',
    )


_shard_config = threading.local()
_shard_config.ignored_keys = None


@contextlib.contextmanager
def ignore_keys(patterns: List[str]):
    try:
        prev_patterns = _shard_config.ignored_keys
        _shard_config.ignored_keys = patterns
        yield
    finally:
        _shard_config.ignored_keys = prev_patterns


def patched_get_checkpoint_shard_files(
    pretrained_model_name_or_path, index_filename, *args, **kwargs
) -> Tuple[List[str], dict]:
    """Same as modeling_utils.get_checkpoint_shard_files(), but does not download shards for the ignored keys."""

    should_ignore_keys = _shard_config.ignored_keys is not None
    tempdir_ctx = tempfile.TemporaryDirectory() if should_ignore_keys else contextlib.nullcontext()
    with tempdir_ctx as tempdir:
        if should_ignore_keys:
            with open(index_filename) as f:
                index = json.load(f)
            n_original_shards = len(set(index["weight_map"].values()))

            index["weight_map"] = {
                param_name: filename
                for param_name, filename in index["weight_map"].items()
                if all(re.search(pattern, param_name) is None for pattern in _shard_config.ignored_keys)
            }
            n_loaded_shards = len(set(index["weight_map"].values()))
            logger.debug(f"Loading {n_loaded_shards} shards out of {n_original_shards}")

            # Replace the original index with a patched JSON, where ignored keys are removed
            index_filename = os.path.join(tempdir, "pytorch_model.bin.index.json")
            with open(index_filename, "w") as f:
                json.dump(index, f)

        return original_get_checkpoint_shard_files(pretrained_model_name_or_path, index_filename, *args, **kwargs)


original_get_checkpoint_shard_files = modeling_utils.get_checkpoint_shard_files
modeling_utils.get_checkpoint_shard_files = patched_get_checkpoint_shard_files