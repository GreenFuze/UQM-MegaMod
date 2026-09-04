# UQM MegaMod AI — POC

**Talk to the aliens. In your own words.**

A proof of concept that replaces *The Ur-Quan Masters*' fixed dialogue menus with
free-text conversation. Instead of picking option 2 of 4, you type what you want to
say — and the character answers in their own voice, from what they actually know at
that point in the story.

### Built on [**UQM MegaMod**](https://github.com/JHGuitarFreak/UQM-MegaMod) by JHGuitarFreak

This is a fork of [github.com/JHGuitarFreak/UQM-MegaMod](https://github.com/JHGuitarFreak/UQM-MegaMod),
which is itself a fork of [The Ur-Quan Masters](https://sc2.sourceforge.net/). MegaMod is
where all the hard work lives - the HD remaster, the options, the decades of fixes. This
fork changes exactly one thing about it. Powered by Claude.

> Looking for MegaMod itself, and its own very long list of features? That readme is
> preserved here as **[README-MegaMod.md](README-MegaMod.md)**. This fork changes one
> thing about MegaMod and inherits everything else from it.

> **This is a proof of concept, not a finished mod.** It has been played mainly through
> two encounters: Fwiffo's first meeting on Pluto, and Commander Hayes at the start of
> the game. Everything else is authored and loads, but has never been played. See
> [Limitations](#limitations) — please read that section before forming an opinion.

---

## The one design rule

The interesting problem here isn't getting a model to talk like Fwiffo. It's stopping
it from *lying to you about the game*.

An AI that can change game state can promise you a ship it can't deliver, unlock a
plot flag you didn't earn, or tell you the Chmmr are ready when they aren't. So it
can't:

> **The game owns all state. The AI owns language and consent, and nothing else.**

Concretely, each turn the encounter exports the actions it is willing to accept right
now. The AI may do exactly three things: pick **one** of those actions, decide whether
the character goes along with it, and write the prose. There is no path from model
output to a game state write. Every choice is re-checked in Python and then again in C
against the actions that were actually offered that turn.

So the model can be wrong, or hallucinate wildly, and the worst it produces is a
character saying something odd. It cannot corrupt your save or make the game
unwinnable.

The second rule follows from the first: a character can only talk about what they know
*yet*. The prompt for a given turn simply doesn't contain the things the story hasn't
unlocked — you can't instruct a model that already knows Star Control II not to spoil
it, so it's never told. 459 game state flags gate this.

---

## Screenshots

<!--
    Capture with F8 in-game; files land in
    %APPDATA%\uqm-megamod\screenshots\
    Drop two or three here and uncomment:

![Fwiffo, first contact](docs/screenshots/fwiffo-first-contact.png)
![Commander Hayes](docs/screenshots/hayes-briefing.png)
-->

*Coming — press <kbd>F8</kbd> in-game to capture; they save to
`%APPDATA%\uqm-megamod\screenshots\`.*

---

## Prerequisites

| Need | Why |
|---|---|
| **Windows** | The game builds 32-bit MSVC only |
| **Python 3.11+**, 64-bit | The AI sidecar. 3.11 is the floor — character files are parsed with `tomllib` |
| **Git** | Fetches the pinned game content |
| **A model to talk to** | Either an API key (Anthropic or OpenAI) or a local model such as Ollama, which is free — see [Choosing a model](#choosing-a-model) |
| **Visual Studio 2022** with *Desktop development with C++* | Only to build the game. Community edition is free; skip with `-SkipBuild` |

Optional, for synthesised speech: several GB of `torch` + `chatterbox-tts`. Voice is
**off by default** and you don't need it.

---

## Install

```powershell
git clone -b ai-edition https://github.com/GreenFuze/UQM-MegaMod.git uqm-megamod
cd uqm-megamod
.\install.ps1     # once: prerequisites, content, build
.\Play.ps1        # every time: configure and play
```

`install.ps1` does the one-time work: prerequisites, the pinned content, the virtual
environment the game looks for, the build, and the sidecar's own preflight - so a broken
install tells you here rather than halfway through a conversation. Safe to re-run; every
step is skipped if already done.

`Play.ps1` is the everyday one. It shows what is configured, what is missing and how to
fix it, then lets you choose an AI, paste a key, turn voice on or off, install what is
needed, test the connection, and play:

```
    AI     : Claude (API key)
    Voice  : off (subtitles)
    Status : ready

    1. Choose which AI answers
    2. Voice: turn ON
    3. Install voice support (large download)
    4. Install / repair the AI packages
    5. Test the connection
    6. Play
```

Settings are saved to `%APPDATA%\uqm-megamod\uqmai.toml`, beside the game's own
configuration. **No environment variables are required** - though if you set one it still
wins, so a one-off run never means editing a file and remembering to edit it back.

```powershell
.\install.ps1 -WithVoice     # also install speech synthesis
.\install.ps1 -SkipBuild     # you already have UrQuanMasters.exe
```

### Doing it by hand

<details>
<summary>Manual steps</summary>

```powershell
# 1. Content, pinned. autocrlf MUST be off - these are binary assets under
#    names git treats as text, and translation silently corrupts them.
git -c core.autocrlf=false clone --depth 1 -b 0.8.5 `
    https://github.com/JHGuitarFreak/UQM-MegaMod-Content.git ..\uqm-megamod-content

# Sanity check: this file must be exactly 30 bytes. 31 means CRLF corruption.
(Get-Item ..\uqm-megamod-content\base\planets\alkali-med.ani).Length

# 2. The sidecar. The game looks for ai\.venv\Scripts\python.exe specifically.
python -m venv ai\.venv
ai\.venv\Scripts\python.exe -m pip install claude-agent-sdk pytest

# 3. Build (Win32 - the 32-bit build is why the AI is a separate process at all)
& "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe" `
    build\msvs2019\UrQuanMastersMegaMod.sln /p:Configuration=Release /p:Platform=Win32
```

</details>

---

## Running

Easiest is `.\Play.ps1`, which configures and launches. To start the game directly:

```powershell
.\UrQuanMasters.exe                 # AI conversation, subtitles - the default
.\UrQuanMasters.exe --ai-voice      # ...with synthesised speech
.\UrQuanMasters.exe --no-ai         # plain MegaMod, no AI at all
.\UrQuanMasters.exe --logfile=game.log   # keep a log when reporting a problem
```

Run it from the repo root — the game finds the sidecar as `ai` relative to the
executable.

**In a conversation:** type and press Enter. <kbd>→</kbd> or <kbd>Enter</kbd> pages
forward, <kbd>Esc</kbd> skips. Replies take
several seconds — you'll see *(transmitting...)* while the character thinks.

---

## Choosing a model

Set `UQMAI_PROVIDER` to one of:

| `UQMAI_PROVIDER` | Talks to | Credentials | Cost |
|---|---|---|---|
| `claude` *(default)* | Anthropic, via the Claude Agent SDK | `ANTHROPIC_API_KEY` | per token |
| `openai` | OpenAI chat completions | `OPENAI_API_KEY` | per token |
| `local` | Any OpenAI-compatible server on your machine — [Ollama](https://ollama.com), llama.cpp, LM Studio, vLLM | none | **free** |
| `claude` + subscription | The Claude CLI you are signed in to | that sign-in | your existing plan |

The last row is **personal use only** and is not a way to distribute this - see
[Cost and licensing](#cost-and-licensing).

Override the endpoint and model for any of them:

```powershell
$env:UQMAI_BASE_URL = 'http://localhost:11434/v1'
$env:UQMAI_MODEL    = 'llama3.1:8b'
```

Because `local` is just a base URL, anything speaking the OpenAI protocol works —
including OpenRouter, or a server on another machine on your network.

**Only Claude has actually been tested.** The OpenAI and local backends share all of
the prompt, contract and validation code with it — a backend supplies one method — so
they should work, but nobody has played the game through them. A smaller local model
will likely be worse at staying in character and at picking the right response; the
safety rule holds regardless, because it is enforced in the game, not the model.

---

## Cost and licensing

**Read this before distributing anything.**

With `claude` or `openai`, conversation is billed to **your own API account** — no key
is bundled. Expect roughly 2–10k input tokens per turn depending on how much of the
story the character has unlocked. With `local` it costs nothing and no data leaves
your machine.

**Neither a Claude Pro/Max nor a ChatGPT Plus/Pro subscription can be used for this.**
Both vendors bill their chat subscriptions separately from their APIs, and neither
permits a third-party program to authenticate as a subscriber. Anthropic's Agent SDK
terms are explicit:

> Unless previously approved, Anthropic does not allow third party developers to offer
> claude.ai login or rate limits for their products, including agents built on the
> Claude Agent SDK. Use the API key authentication methods described in the Quickstart
> instead.

OpenAI is the same story from the other side: ChatGPT Plus and Pro do not include API
access or let you mint a key, so a Codex or ChatGPT login cannot drive this either.

**On your own machine, for yourself, is a different question.** `Play.ps1` offers
"Claude, with my subscription", which routes answers through the Claude CLI you are
already signed in to. The word the terms turn on is *offer*: using the tool you already
pay for, on your own account, is between you and Anthropic; shipping a build that points
other people at theirs is what is forbidden. So it is never the default, it is labelled
where it is offered, and a release must not enable it.

Use of the Agent SDK is governed by Anthropic's
[Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms) when it
powers something you make available to others. An earlier version of this project
leaned on the signed-in Claude Code CLI; that was wrong and has been removed.

Anthropic runs a *Claude for Open Source* program, but it grants a **subscription to
the maintainer**, not distributable API access — useful for developing this, no help
for the person playing it. If per-token cost is the obstacle, the answer is
`UQMAI_PROVIDER=local`.

---

## Limitations

Being honest about a proof of concept is the whole point of calling it one.

**What has actually been played**

- Fwiffo's first encounter on Pluto
- Commander Hayes at the start of the game (the pre-starbase conversation)

**What has not**

- **25 of 27 characters.** They are all authored — 27 character files, 239 knowledge
  entries, and the test suite checks that every game flag and every phrase key they
  name really exists — but nobody has held a conversation with most of them.
- **The starbase Commander.** Hayes has two conversations: a 94-phrase one before the
  starbase exists and a 267-phrase one after. Most of the authoring effort went into
  the second, and it has never been exercised in play.
- Conversation memory across sessions, and the end-of-conversation summary. Both
  implemented, neither confirmed working in a real game.
- Replaying the last generated line with <kbd>←</kbd>. Implemented, never confirmed.
- **The OpenAI and local backends.** Written and unit-tested, never played through.
  Only Claude has been used in a real game.

**Known rough edges**

- **Latency.** A reply takes several seconds; an action that fires costs two model
  calls, so longer. There's a *(transmitting...)* indicator, and that's all.
- **Pacing.** Generated lines are held ten seconds a page, which is deliberately
  generous. Press <kbd>→</kbd> to move on.
- **The AI picks your line for you.** Your free text is matched to the nearest action
  the encounter offered, and that authored line may carry specifics you never said.
  This is handled — the narration is told not to hand claims back to you that you
  didn't make — but it's the least settled part of the design.
- **Length.** Characters sometimes answer at more length than the original ever would.
- **No voice for generated text unless you install synthesis.** Subtitles are carried
  on silence, so a character's mouth moves without sound. This is intentional: the
  alternative was playing a real recording of them saying something else.
- **Not translated.** The prompts and character files are English only.

**Not a limitation, worth stating anyway:** the AI cannot break your save. See
[the one design rule](#the-one-design-rule).

---

## How it works

```
UrQuanMasters.exe  (32-bit)                 ai\.venv\python.exe  (64-bit)
┌────────────────────────────┐              ┌──────────────────────────────┐
│ comm.c                     │  NDJSON      │ sidecar                      │
│  • exports offered actions │─── stdio ───▶│  • builds the character       │
│  • dispatches the choice   │              │    prompt for this turn      │
│  • speaks the outcome      │◀─────────────│  • calls Claude              │
│  • re-validates in C       │              │  • validates the reply       │
└────────────────────────────┘              └──────────────────────────────┘
```

The sidecar is a separate process because the game is a 32-bit build and can't host a
64-bit Python. It's started before anything is drawn, so a missing prerequisite is one
readable refusal at startup instead of a conversation that appears to hang.

Each character's knowledge comes from two places: their own authored dialogue, mined
from the original game's phrase tables (27 characters, 3,377 phrases, ~88,000 words of
NPC dialogue), plus a hand-written file in `ai/characters/*.toml` giving their voice,
what they know when, and what they genuinely don't.

Run the tests with:

```powershell
cd ai; .venv\Scripts\python.exe -m pytest tests\ -q   # 283 tests
```

---

## Credits

This is a fork of a fork, and almost none of it is mine.

- **[UQM MegaMod](https://github.com/JHGuitarFreak/UQM-MegaMod)** by **JHGuitarFreak**
  — the base this is built on, and an enormous body of work: HD remastering, quality
  of life, options and features far beyond the original. Everything here rides on it.
- **[The Ur-Quan Masters](https://sc2.sourceforge.net/)** — the open source port that
  kept the game alive and playable for over twenty years.
- **Paul Reiche III** and **Fred Ford** — for *Star Control II*, and for releasing the
  source and content that made all of this possible.
- **Toys for Bob** and **Accolade** — the 1992 original.
- Every line of dialogue an alien speaks here was written by the original authors. The
  AI rewords; it does not invent the game.

## Licence

Code is **GPL v2**, inherited from The Ur-Quan Masters — see [LICENSE](LICENSE).
Game content is under its own terms; see the MegaMod content repository.
Use of the Claude Agent SDK is governed by
[Anthropic's Commercial Terms](https://www.anthropic.com/legal/commercial-terms).

This project is not affiliated with or endorsed by Anthropic, Toys for Bob, or the UQM
or MegaMod teams.
