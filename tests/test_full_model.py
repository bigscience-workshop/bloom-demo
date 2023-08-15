import peft
import pytest
import torch
import transformers
from hivemind import get_logger

from petals import AutoDistributedModelForCausalLM
from test_utils import *

logger = get_logger(__name__)


@pytest.fixture
def tokenizer():
    # We set use_fast=False since LlamaTokenizerFast is slow on load
    return transformers.AutoTokenizer.from_pretrained(MODEL_NAME, use_fast=False)


@pytest.fixture
def model():
    return AutoDistributedModelForCausalLM.from_pretrained(
        MODEL_NAME, initial_peers=INITIAL_PEERS, torch_dtype=torch.float32
    )


@pytest.fixture
def ref_model():
    return transformers.AutoModelForCausalLM.from_pretrained(
        REF_NAME, low_cpu_mem_usage=True, torch_dtype=torch.float32
    )


@pytest.mark.forked
@pytest.mark.parametrize("use_peft", (True, False) if ADAPTER_NAME else (False,))
@pytest.mark.parametrize("pass_empty_tensors", (True, False))
def test_full_model_exact_match(tokenizer, model, ref_model, use_peft, pass_empty_tensors, atol=1e-3):
    if use_peft:
        model.config.active_adapter = ADAPTER_NAME

        ref_model = peft.PeftModel.from_pretrained(ref_model, ADAPTER_NAME)
        ref_model.train(False)

    test_inputs = tokenizer("A quick brown fox was minding its own buisness", return_tensors="pt")["input_ids"]

    with torch.inference_mode():
        parallel_outputs = model.forward(test_inputs).logits
        assert torch.all(torch.isfinite(parallel_outputs))
        logger.info("Forward outputs are finite")

        embs = model.transformer.word_embeddings(test_inputs)
        embs = model.transformer.word_embeddings_layernorm(embs)
        recurrent_outputs = []
        with model.transformer.h.inference_session(max_length=embs.shape[1]) as sess:
            if pass_empty_tensors:
                recurrent_outputs.append(sess.step(torch.empty(1, 0, model.config.hidden_size)))

            for t in range(embs.shape[1]):
                if t == 4:
                    recurrent_outputs.append(sess.step(embs[:, 4:9, :]))
                elif 4 < t < 9:
                    continue
                else:
                    recurrent_outputs.append(sess.step(embs[:, t : t + 1, :]))

                if t == 2 and pass_empty_tensors:
                    recurrent_outputs.append(sess.step(torch.empty(1, 0, model.config.hidden_size)))
                    recurrent_outputs.append(sess.step(torch.empty(1, 0, model.config.hidden_size)))

        recurrent_outputs = torch.cat(recurrent_outputs, dim=1)
        recurrent_outputs = model.transformer.ln_f(recurrent_outputs)
        recurrent_outputs = model.lm_head(recurrent_outputs)
        assert torch.allclose(
            recurrent_outputs, parallel_outputs, rtol=0, atol=atol
        ), "Inference differs from forward pass"

        ref_outputs = ref_model.forward(test_inputs).logits.float()
        assert torch.allclose(ref_outputs, parallel_outputs, rtol=0, atol=atol), "Outputs are not identical to HF"


def make_generate_calls(model, inputs, *, max_new_tokens, multiple_calls=False, **kwargs):
    if not multiple_calls:
        return model.generate(inputs, max_new_tokens=max_new_tokens, **kwargs)

    with model.inference_session(max_length=inputs.shape[1] + max_new_tokens) as sess:
        return torch.cat(
            [
                # Sessions provided both explicitly and implicitly should work
                model.generate(inputs, max_new_tokens=1, **kwargs, session=sess),
                model.generate(None, max_new_tokens=max_new_tokens - 2, **kwargs),
                model.generate(None, max_new_tokens=1, **kwargs),
            ],
            dim=1,
        )


@pytest.mark.forked
def test_greedy_generation(tokenizer, model, ref_model, max_new_tokens=4):
    inputs_single = tokenizer("A cat sat on a mat", return_tensors="pt")["input_ids"]

    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    inputs_batch = tokenizer(["A cat sat on a mat", "A dog sat on a mat"], return_tensors="pt", padding=True)[
        "input_ids"
    ]

    for multiple_calls in [False, True]:
        for inputs in [inputs_single, inputs_batch]:
            outputs = make_generate_calls(
                model, inputs, max_new_tokens=max_new_tokens, multiple_calls=multiple_calls, do_sample=False
            )
            ref_outputs = ref_model.generate(inputs, max_new_tokens=max_new_tokens, do_sample=False)
            assert torch.allclose(
                outputs, ref_outputs
            ), f"Greedy generation is not identical to HF with {multiple_calls=}, {inputs.shape=}"


@pytest.mark.forked
def test_sampling(tokenizer, model, ref_model, max_new_tokens=4):
    inputs_single = tokenizer("A cat sat on a mat", return_tensors="pt")["input_ids"]

    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    inputs_batch = tokenizer(["A cat sat on a mat", "A dog sat on a mat"], return_tensors="pt", padding=True)[
        "input_ids"
    ]

    for sampling_options in [
        dict(do_sample=True),
        dict(do_sample=True, temperature=0.5),
        dict(do_sample=True, temperature=0.5, top_k=5),
        dict(do_sample=True, temperature=0.5, top_k=5, top_p=0.9),
        dict(do_sample=True, temperature=0.5, top_k=5, top_p=0.9, multiple_calls=True),
    ]:
        multiple_calls = sampling_options.pop("multiple_calls", False)
        for inputs in [inputs_single, inputs_batch]:
            torch.manual_seed(0)
            outputs = make_generate_calls(
                model, inputs, max_new_tokens=max_new_tokens, multiple_calls=multiple_calls, **sampling_options
            )

            torch.manual_seed(0)
            ref_outputs = ref_model.generate(inputs, max_new_tokens=max_new_tokens, **sampling_options)

            assert torch.allclose(
                outputs, ref_outputs
            ), f"Sampling is not identical to HF with {sampling_options=}, {inputs.shape=}"


@pytest.mark.forked
def test_beam_search_generation(tokenizer, model, ref_model, max_new_tokens=4, num_beams=6):
    inputs = tokenizer("A cat sat on a mat", return_tensors="pt")["input_ids"]

    for multiple_calls in [False, True]:
        outputs = make_generate_calls(
            model,
            inputs,
            max_new_tokens=max_new_tokens,
            multiple_calls=multiple_calls,
            num_beams=num_beams,
            do_sample=False,
        )
        ref_outputs = ref_model.generate(inputs, max_new_tokens=max_new_tokens, num_beams=num_beams, do_sample=False)
        assert torch.allclose(
            outputs, ref_outputs
        ), f"Beam search results are not identical to HF with {multiple_calls=}"
