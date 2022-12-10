"""
A prototype TensorParallel wrapper that works without torchrun. Original code by @BlackSamorez and @IaroslavLisniak .
This code is here temporarily, with authors' permission, until they make it publicly available otherwise.

The original code can be found here: https://github.com/BlackSamorez/petals_local_parallel , using MIT license
https://github.com/BlackSamorez/petals_local_parallel/blob/496e4a8ea641ff641e59309445ddc9fe0d7960cd/LICENCE
"""
from typing import Union, Iterator, Callable, Dict, Tuple

import torch
import torch.nn as nn
import re

Arg = Union[int, str]


class SlicingConfig:
    def __init__(self, tensor_rules: dict, module_rules: dict):
        self.tensor_rules = tensor_rules
        self.module_rules = module_rules


def slice_weight_vertical(tensor: torch.Tensor, rank: int, world_size: int) -> torch.Tensor:
    assert (tensor.shape[-2] % world_size == 0)
    slice_size = tensor.shape[-2] // world_size

    return tensor[..., rank * slice_size: (rank + 1) * slice_size, :]


def slice_bias_vertical(tensor: torch.Tensor, rank: int, world_size: int) -> torch.Tensor:
    assert (tensor.shape[-1] % world_size == 0)
    slice_size = tensor.shape[-1] // world_size

    return tensor[rank * slice_size: (rank + 1) * slice_size]


def slice_weight_horizontal(tensor: torch.Tensor, rank: int, world_size: int) -> torch.Tensor:
    assert (tensor.shape[-1] % world_size == 0)
    slice_size = tensor.shape[-1] // world_size

    return tensor[..., rank * slice_size: (rank + 1) * slice_size]


def slice_bias_horizontal(tensor: torch.Tensor, rank: int, world_size: int) -> torch.Tensor:
    return tensor / world_size


SLICING_RULES = {
    ("vertical", "weight"): slice_weight_vertical,
    ("vertical", "bias"): slice_bias_vertical,
    ("horizontal", "weight"): slice_weight_horizontal,
    ("horizontal", "bias"): slice_bias_horizontal,
}


def slice_tensors(key_parameter_iterator: Iterator[Tuple[str, nn.Parameter]], tensor_rules: Dict[Arg, str],
                  rank: int, world_size: int):
    regular_rules = [(re.compile(key), value) for key, value in tensor_rules.items()]

    with torch.no_grad():
        for name, param in key_parameter_iterator:
            for pattern, rule in regular_rules:
                if pattern.search(name) is not None:
                    name_ending = name.split('.')[-1]
                    param.data = SLICING_RULES[rule, name_ending](param.data, rank=rank, world_size=world_size)


def process_input(rules: Dict[Arg, str], rank: int, world_size: int, *args, **kwargs):
    extended_kwargs = dict(kwargs)
    extended_kwargs.update(enumerate(args))

    for target, action in rules.items():
        action_type, *opts = action.split()
        if action_type == "cut":
            extended_kwargs[target] = extended_kwargs[target].tensor_split(world_size, dim=opts[0])[rank]
        elif action_type == "scale":
            extended_kwargs[target] = extended_kwargs[target] / world_size
        else:
            raise Exception(f"unexpected action {action_type}")

    args = [extended_kwargs.pop(i) for i in range(len(args))]
    return args, extended_kwargs


def process_output(output, rules: Dict[Arg, Callable[[torch.Tensor, int], torch.Tensor]], rank: int):
    if isinstance(output, torch.Tensor):
        return process_output([output], rules, rank)[0]
    for target, action in rules.items():
        output[target] = action(output[target], rank)
    return output


def process_attr(module: nn.Module, rules: Dict[Arg, str], rank: int, world_size: int):
    for attr, action in rules.items():
        if action == "scale_int":
            setattr(module, attr, getattr(module, attr) // world_size)
        else:
            raise NotImplementedError(action)


class ParallelLayerWrapper(nn.Module):
    def __init__(self, module: nn.Module, module_rules: dict, rank: int, world_size: int):
        super().__init__()
        self.module = module
        process_attr(self.module, module_rules["attributes"], rank=rank, world_size=world_size)

        self.input_rules = module_rules["input"]
        self.output_rules = module_rules["output"]

        self.rank = rank
        self.world_size = world_size

    def forward(self, *args, **kwargs):
        args, kwargs = process_input(self.input_rules, self.rank, self.world_size, *args, **kwargs)
        output = self.module(*args, **kwargs)
        return process_output(output, self.output_rules, self.rank)


def wrap_submodules(model: nn.Module, module_rules: dict, rank: int, world_size: int):
    unique_wrappers = {}
    with torch.no_grad():
        for name, module in model.named_modules():
            for pattern, rule in module_rules.items():
                if re.search(pattern, name) is not None:
                    unique_wrappers[module] = ParallelLayerWrapper(module, rule, rank=rank, world_size=world_size)

    for parent in list(model.modules()):
        for child_name, child in list(parent.named_children()):
            if child in unique_wrappers:
                setattr(parent, child_name, unique_wrappers[child])
