<p align="center">
    <img src="https://i.imgur.com/7eR7Pan.png" width="400"><br>
    Easy way to run 100B+ language models without high-end GPUs<br>
    by collaborating with researchers across the world<br><br>
</p>

Generate text using distributed BLOOM and fine-tune it for your own tasks:

```python
from petals.client import DistributedBloomForCausalLM

# Embeddings & prompts are on your device, BLOOM blocks are distributed across the Internet
model = DistributedBloomForCausalLM.from_pretrained("bigscience/bloom-petals", tuning_mode="ptune")

inputs = tokenizer("A cat sat", return_tensors="pt")["input_ids"]
outputs = model.generate(inputs, max_new_tokens=5)
print(tokenizer.decode(remote_outputs[0]))  # A cat sat on a mat...

# Training (updates only prompts or adapters hosted locally)
optimizer = torch.optim.AdamW(model.parameters())
for input_ids, labels in data_loader:
    outputs = model.forward(input_ids)
    loss = cross_entropy(outputs.logits, labels)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
```

<p align="center">
    🚀 &nbsp;<b><a href="https://petals.ml/">Try now in Colab</a></b>
</p>

Connect your own GPU and increase Petals capacity:

```bash
(conda) $ pip install git+https://github.com/bigscience-workshop/petals
(conda) $ python -m petals.cli.run_server bigscience/bloom-petals
```

💬 If you have any issues or feedback, tell us in our [**Discord**](https://petals.ml/)!

Check out more examples:

- Training a personified chatbot: [examples/prompt-tuning-personachat.ipynb](./examples/prompt-tuning-personachat.ipynb)
- Fine-tuning BLOOM for text semantic classification: [examples/prompt-tuning-sst2.ipynb](./examples/prompt-tuning-sst2.ipynb)

## How it works?

- **Petals** runs inference or fine-tunes large language models like [BLOOM-176B](https://huggingface.co/bigscience/bloom) by joining compute resources with people all over the Internet.
- One participant with weak GPU can load a small part of the model, then team up with people serving the other parts to run inference or fine-tuning.
- This way, one inference step takes ≈ 1 sec — 10x faster than possible with offloading. Enough for chatbots and other interactive apps.
- Beyond classic language model APIs — you can employ any fine-tuning and sampling methods by executing custom paths through the model or accessing its hidden states. This combines the comforts of an API with the flexibility of PyTorch.

<p align="center">
    <img src="https://i.imgur.com/RTYF3yW.png" width="800">
</p>

<p align="center">
    📜 &nbsp;<b><a href="https://arxiv.org/pdf/2209.01188.pdf">Read paper</a></b>
</p>

### 📋 Terms of use

Before using Petals to run a language model, please make sure that you are familiar with its terms of use, risks, and limitations. In case of BLOOM, they are described in its [model card](https://huggingface.co/bigscience/bloom) and [license](https://huggingface.co/spaces/bigscience/license).

### 🔒 Privacy and security

If you work with sensitive data, you should only use a private swarm (or a subset of servers in the public swarm) hosted by people and institutions you trust, who are authorized to process this data.

This is important because it's technically possible for peers serving model layers to recover input data or model outputs. Also, if there are malicious peers, they may alter their outputs to influence the model outputs. See a more detailed discussion in Section 4 of our [paper](https://arxiv.org/pdf/2209.01188.pdf).

## FAQ

1. **What's the motivation for people to host model layers in the public swarm?**

    People who run inference and fine-tuning themselves get a certain speedup if they host a part of the model locally. Some may be also motivated to "give back" to the community helping them to run the model (similarly to how [BitTorrent](https://en.wikipedia.org/wiki/BitTorrent) users help others by sharing data they have already downloaded).

    Since it may be not enough for everyone, we are also working on introducing explicit __incentives__ ("bloom points") for people donating their GPU time to the public swarm. Once this system is ready, people who earned these points will be able to spend them on inference/fine-tuning with higher priority or increased security guarantees, or (maybe) exchange them for other rewards.

2. **Why is the platform named "Petals"?**

    "Petals" is a metaphor for people serving different parts of the model. Together, they host the entire language model &mdash; [BLOOM](https://huggingface.co/bigscience/bloom).

    While our platform focuses on BLOOM now, we aim to support more [foundation models](https://arxiv.org/abs/2108.07258) in future.

## Installation

Here's how to install Petals with conda:
```
conda install pytorch torchvision torchaudio cudatoolkit=11.3 -c pytorch
pip install git+https://github.com/bigscience-workshop/petals
```

This script uses Anaconda to install cuda-enabled PyTorch.
If you don't have anaconda, you can get it from [here](https://www.anaconda.com/products/distribution).
If you don't want anaconda, you can install PyTorch [any other way](https://pytorch.org/get-started/locally/).
If you want to run models with 8-bit weights, please install **PyTorch with CUDA 11** or newer for compatility with [bitsandbytes](https://github.com/timDettmers/bitsandbytes).

__OS support:__ Currently, Petals only supports Linux operating systems. On Windows 11, you can run Petals with GPU enabled inside WSL2 ([read more](https://learn.microsoft.com/en-us/windows/ai/directml/gpu-cuda-in-wsl)).
For macOS, you can *probably* run everything normally if you manage to install dependencies, but we do not guarantee this.


## 🚀 Getting Started

This is a toy example running on a local machine without GPU and with a tiny model.
For a detailed instruction with larger models, see ["Launch your own swarm"](https://github.com/bigscience-workshop/petals/wiki/Launch-your-own-swarm).

First, run a couple of servers, each in a separate shell. To launch your first server, run:
```bash
python -m petals.cli.run_server bloom-testing/test-bloomd-560m-main --num_blocks 8 --torch_dtype float32 \
  --host_maddrs /ip4/127.0.0.1/tcp/31337   # use port 31337, local connections only
```

This server will host 8 (out of 24) blocks of a [tiny 560M version](https://huggingface.co/bloom-testing/test-bloomd-560m-main) of the BLOOM model that was converted for Petals.

> If you'd like to run a swarm of servers with the full BLOOM straight away, please see [this instruction](https://github.com/bigscience-workshop/petals/wiki/Launch-your-own-swarm) (you'll need several GPUs!). To run a different model, see [this wiki page](https://github.com/bigscience-workshop/petals/wiki/Run-a-custom-model-with-PETALS).

Once the server has started, it will print out a ton of information, including an important line like this:

```bash
Mon Day 01:23:45.678 [INFO] Running DHT node on ['/ip4/127.0.0.1/tcp/31337/p2p/ALongStringOfCharacters'], initial peers = []
```

You can use this address (`/ip4/whatever/else`) to connect additional servers. Open another terminal and run:

```bash
python -m petals.cli.run_server bloom-testing/test-bloomd-560m-main --num_blocks 8 --torch_dtype float32 \
  --host_maddrs /ip4/127.0.0.1/tcp/0 \
  --initial_peers /ip4/127.0... # <-- TODO: Copy the address of another server here
# e.g. --initial_peers /ip4/127.0.0.1/tcp/31337/p2p/QmS1GecIfYouAreReadingThisYouNeedToCopyYourServerAddressCBBq
```

You can assign `--initial_peers` to one or multiple addresses of other servers, not necessarily the first one.
The only requirement is that at least one of them is running at the time.

Before you proceed, __please run 3 servers__ for a total of 24 blocks (3x8). If you are running a different model,
make sure your servers have enough total `--num_blocks` to cover that model.

Once your have enough servers, you can use them to train and/or inference the model:
```python
import torch
import torch.nn.functional as F
from transformers import BloomTokenizerFast
from petals.client import DistributedBloomForCausalLM

initial_peers = [TODO_put_one_or_more_server_addresses_here]  # e.g. ["/ip4/127.0.0.1/tcp/more/stuff/here"]
tokenizer = BloomTokenizerFast.from_pretrained("bloom-testing/test-bloomd-560m-main")
model = DistributedBloomForCausalLM.from_pretrained(
  "bloom-testing/test-bloomd-560m-main", initial_peers=initial_peers, low_cpu_mem_usage=True, torch_dtype=torch.float32
)  # this model has only embeddings / logits, all transformer blocks rely on remote servers


inputs = tokenizer("a cat sat", return_tensors="pt")["input_ids"]
remote_outputs = model.generate(inputs, max_length=10)
print(tokenizer.decode(remote_outputs[0]))  # "a cat sat in the back of the car,"

# "train" input embeddings by backprop through distributed transformer blocks
model.transformer.word_embeddings.weight.requires_grad = True
outputs = model.forward(input_ids=inputs)
loss = F.cross_entropy(outputs.logits.flatten(0, 1), inputs.flatten())
loss.backward()
print("Gradients (norm):", model.transformer.word_embeddings.weight.grad.norm())
```

Of course, this is a simplified code snippet. For actual training, see the example notebooks with "deep" prompt-tuning:
- Simple text semantic classification: [examples/prompt-tuning-sst2.ipynb](./examples/prompt-tuning-sst2.ipynb)
- A personified chatbot: [examples/prompt-tuning-personachat.ipynb](./examples/prompt-tuning-personachat.ipynb)

Here's a [more advanced tutorial](https://github.com/bigscience-workshop/petals/wiki/Launch-your-own-swarm) that covers 8-bit quantization and best practices for running Petals.

## 🛠️ Development

Petals uses pytest with a few plugins. To install them, run:

```python
git clone https://github.com/bigscience-workshop/petals.git && cd petals
pip install -e .[dev]
```

To run minimalistic tests, spin up some servers:

```bash
export MODEL_NAME=bloom-testing/test-bloomd-560m-main
export INITIAL_PEERS=/ip4/127.0.0.1/tcp/31337/p2p/QmS9KwZptnVdB9FFV7uGgaTq4sEKBwcYeKZDfSpyKDUd1g
python -m petals.cli.run_server $MODEL_NAME --block_indices 0:12 --throughput 1 --torch_dtype float32 \
  --identity tests/test.id --host_maddrs /ip4/127.0.0.1/tcp/31337  &> server1.log &
sleep 5  # wait for the first server to initialize DHT
python -m petals.cli.run_server $MODEL_NAME --block_indices 12:24 --throughput 1 --torch_dtype float32 \
  --initial_peers /ip4/127.0.0.1/tcp/31337/p2p/QmS9KwZptnVdB9FFV7uGgaTq4sEKBwcYeKZDfSpyKDUd1g &> server2.log &

tail -f server1.log server2.log  # view logs for both servers
# after you're done, kill servers with 'pkill -f petals.cli.run_server'
```

Then launch pytest:

```
export MODEL_NAME=bloom-testing/test-bloomd-560m-main REF_NAME=bigscience/bloom-560m
export INITIAL_PEERS=/ip4/127.0.0.1/tcp/31337/p2p/QmS9KwZptnVdB9FFV7uGgaTq4sEKBwcYeKZDfSpyKDUd1g
PYTHONPATH=. pytest tests --durations=0 --durations-min=1.0 -v
```

The automated tests use a more complex server configuration that can be found [here](https://github.com/bigscience-workshop/petals/blob/main/.github/workflows/run-tests.yaml).

### Code style

We use [black](https://black.readthedocs.io/en/stable/the_black_code_style/current_style.html) and [isort](https://pycqa.github.io/isort/) for all pull requests.
Before commiting your code, simply run `black . && isort .` and you will be fine.

--------------------------------------------------------------------------------

<p align="center">
    This project is a part of the <a href="https://bigscience.huggingface.co/">BigScience</a> research workshop.
</p>
<p align="center">
    <img src="https://petals.ml/bigscience.png" width="150">
</p>
