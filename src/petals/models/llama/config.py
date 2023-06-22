import os
from typing import Optional, Union

from hivemind import get_logger
from transformers.models.llama import LlamaConfig
from transformers.models.llama.modeling_llama import LlamaAttention

from petals.client.lm_head import LMHeadConfig
from petals.client.ptune import PTuneConfig
from petals.client.routing.sequence_manager import SequenceManagerConfig
from petals.models.llama.block import WrappedLlamaBlock
from petals.utils.auto_config import AutoDistributedConfig

logger = get_logger(__name__)


class DistributedLlamaConfig(LlamaConfig, SequenceManagerConfig, PTuneConfig, LMHeadConfig):
    block_class = WrappedLlamaBlock
    attn_class = LlamaAttention
    block_prefix = "model.layers"

    @classmethod
    def from_pretrained(
        cls, model_name_or_path: Union[str, os.PathLike, None], *args, dht_prefix: Optional[str] = None, **kwargs
    ):
        loading_from_repo = model_name_or_path is not None and not os.path.isdir(model_name_or_path)
        if loading_from_repo and dht_prefix is None:
            dht_prefix = str(model_name_or_path)
            if "/" in dht_prefix:  # If present, strip repository name to merge blocks hosted by different accounts
                dht_prefix = dht_prefix[dht_prefix.rfind("/") + 1 :]
            logger.info(f"Using DHT prefix: {dht_prefix}")
        return super().from_pretrained(model_name_or_path, *args, dht_prefix=dht_prefix, **kwargs)


AutoDistributedConfig.register(DistributedLlamaConfig)
