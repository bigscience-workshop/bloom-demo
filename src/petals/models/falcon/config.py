import os
from typing import Optional, Union

from hivemind import get_logger
from transformers.models.falcon import FalconConfig
from transformers.models.falcon.modeling_falcon import FalconAttention

from petals.client.config import ClientConfig
from petals.client.lm_head import LMHeadConfig
from petals.client.ptune import PTuneConfig
from petals.models.falcon.block import WrappedFalconBlock

logger = get_logger(__name__)


class DistributedFalconConfig(FalconConfig, ClientConfig, PTuneConfig, LMHeadConfig):
    block_class = WrappedFalconBlock
    attn_class = FalconAttention
    block_prefix = "transformer.h"

    @property
    def num_key_value_groups(self) -> int:
        if self.new_decoder_architecture or not self.multi_query:
            return self.num_attention_heads // self.num_kv_heads
        return 1

    @classmethod
    def from_pretrained(
        cls, model_name_or_path: Union[str, os.PathLike, None], *args, dht_prefix: Optional[str] = None, **kwargs
    ):
        loading_from_repo = model_name_or_path is not None and not os.path.isdir(model_name_or_path)
        if loading_from_repo and dht_prefix is None:
            dht_prefix = str(model_name_or_path)
            dht_prefix = dht_prefix.split("/")[-1]  # Use only repo name to merge blocks hosted by different accounts
            dht_prefix = dht_prefix.replace(".", "-")
            logger.info(f"Using DHT prefix: {dht_prefix}")
        return super().from_pretrained(model_name_or_path, *args, dht_prefix=dht_prefix, **kwargs)
