# User0332/rewards-farmer

Automation for MS Rewards based on [https://youtu.be/4qdPcMNaioA](https://youtu.be/4qdPcMNaioA).

# Running Instructions

Clone the repository.

```sh
git clone https://github.com/User0332/rewards-farmer
```

Enter the root directory of the repository and create a wordlist named `nouns.txt` which will contain seed words for the LLM to complete 20 searches. The wordlist should be separated by newline

```sh
cd rewards-farmer
cp /path/to/my_amazing_wordlist.txt nouns.txt
```

You should also have an Ollama account created (for the LLM), the `ollama` tool installed, and you should have signed in to the Ollama CLI via the command line using `ollama signin`. This project will use a minimal amount of Ollama cloud usage using `gemma4:cloud`. If you wish to use a different model, please change the `model` parameter in the `get_ollama_response` function in `src/llm_utils.py`.

Activate the virtual environment & install dependencies (you may have to use `python -m poetry` instead of `poetry`).
You must have Python 3.14+ and Poetry installed.

Windows (PowerShell)
```sh
poetry install
iex (poetry env activate)
```

*nix (Bash)
```sh
poetry install
eval $(poetry env activate)
```

You must also have a [webdriver for Microsoft Edge](https://learn.microsoft.com/en-us/microsoft-edge/webdriver/?tabs=c-sharp) installed. If you already have the Edge Browser installed, you probably have this component as well.

Change the profile directory in `src/constants.py` to `Default`. If this signs you in to a global profile that you do not want to use for automation, then you can create a new profile from within the webdriver instance manually and then change the `PROFILE_NAME` constant back to `Profile 1` (or the equivalent number).

Run main.py (`python src/main.py`, it must be run from the root directory so the relative paths work out), wait for the page to launch, and then CTRL-C to quit the application immediately. Sign in to the created profile with your Microsoft account on both Bing and `rewards.bing.com`.

Close all webdriver browser instances. Run `main.py` again; the automation should start working.

Please open up a GitHub issue if you run into any difficulties.