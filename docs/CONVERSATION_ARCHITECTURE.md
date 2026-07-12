# Eve Conversation Engine — Architecture & Design

> **Status:** design, not yet built. Top-priority roadmap item (2026-07-11).
> **Scope of this doc:** the target architecture for Eve's conversation system,
> the audit that motivates it, the state chart, the event/response contracts,
> and a feature-by-feature migration map. No behavior changes ship from this
> document — it is the plan the implementation follows and the reference every
> future conversational feature integrates against.

---

## 1. Why this exists

Today Eve is a wake-word-gated command parser: say "Hey Jarvis", speak one
command, get one reply, repeat. If speech recognition misfires, an intent is
ambiguous, a slot is missing, or a handler errors, the interaction ends and the
user must re-wake Eve. Multi-turn machinery exists in the code but is unreachable
without a wake word between every turn.

The goal is a **Conversation Engine**: a single owner of conversational state
that keeps Eve engaged across turns, recovers from failure instead of ending,
asks follow-up questions, disambiguates by confidence, and maintains context
until a conversation genuinely concludes — closer to Alexa/Siri/ChatGPT Voice
than a stateless parser.

---

## 2. Audit of the current architecture

### 2.1 Flow today

1. **Audio** — `core/listener.py:52` `run()`: infinite loop
   `_wait_for_wake_word()` → `_record_command()` (records until ~2.5 s silence,
   30 s cap) → `on_command(audio)` → set cooldown → **back to wait-for-wake-word**.
2. **Turn** — `main.py:106` `on_command`: transcribe → `dispatch(text)` →
   render/speak.
3. **Routing** — `core/dispatcher.py:790` `dispatch`: strip wake prefix →
   `_handle_confirmation` → `_handle_converse` → `Mode`-aware → custom commands →
   aliases → PREEMPT skills → registry → skills → `_guess_dispatch`
   (did-you-mean) → learned tier → LLM fallback → "Not recognized."

### 2.2 Where conversation state lives

One module-global singleton, `core/session.py:74` `_session = Session()`:

| Field | Purpose |
|-------|---------|
| `mode` (`Mode` enum) | IDLE / LISTING / PLAYING / BROWSING |
| `pending_confirm` | single-turn yes/no `(callable, args, label)` |
| `converse` (`Converse`) | multi-turn claim: `handler` + `turns` + `ttl` |
| `last_action` (`LastAction`) | "undo it / cancel that / go back" target |
| `video_list`, `site_list`, `selected_*` | list-selection state |

The **data seeds** of a conversation engine already exist. What is missing is a
**control model** that owns them coherently.

### 2.3 The central defect — every turn is wake-word-gated

`core/listener.py:55-66`: each loop iteration *begins* with
`_wait_for_wake_word()`, and after `on_command` returns it loops straight back.
**There is no follow-up listening window.** So every multi-turn mechanism —
`pending_confirm`, `converse`, `last_action` — requires the user to re-say the
wake word to answer. The `close_app` "Did you mean chrome?" suggestion is
literally unusable hands-free for this reason. This is the root cause of "the
interaction ends and the user has to say Hey Eve again."

### 2.4 Where interactions terminate unnecessarily

| # | Location | Problem |
|---|----------|---------|
| 1 | `listener.py:66` (turn boundary) | mic never re-opens without the wake word |
| 2 | `main.py:187` (exception path) | any handler error → "something went wrong" → idle; no retry/recovery |
| 3 | `main.py:155` (`Silent`) | shown on HUD, **never spoken** — the existing "Did you mean X?" is inaudible |
| 4 | `session.py:99` `reset()` | wipes the whole Session; coarse, clobbers unrelated pending state |
| 5 | `Verified.on_fail` | honest failure is terminal — no "try again / do it differently?" |
| 6 | dispatch end | "Not recognized" dead-ends; no clarification or escalation |

### 2.5 Technical debt

Three overlapping multi-turn mechanisms — `pending_confirm`, `converse`, `Mode`
— plus `last_action`, none sharing a lifecycle, timeout policy, or state model.
Confirmation logic is split across `_handle_confirmation` + session;
disambiguation across `_guess_dispatch` + session; state-awareness is scattered
`Mode` checks in both dispatcher and main (~30 coupling sites). This is already
the "collection of patches" we must stop accreting.

**Verdict:** the data model is a usable seed; the control model is absent.
Build *on* session.py's data, replace the scattered control flow.

---

## 3. Options considered

| Approach | Maintainability | Extensibility | Testability | Recovery / multi-turn | Interrupt / nest | Complexity | Fit |
|---|---|---|---|---|---|---|---|
| Flat FSM | ok small | poor (shared edges duplicate per-state) | excellent | ok | needs external stack | low | rigid alone |
| **Hierarchical SM** | strong (shared behavior on a parent superstate) | strong (children inherit recovery/timeout) | excellent | excellent | superstate + stack | medium | **core** |
| Actor / message | good large | strong | harder (concurrency) | strong | natural | high | over-engineered¹ |
| Event-driven | good | strong (heterogeneous triggers share intake) | good | strong | good | medium | **intake layer** |
| **Hybrid (chosen)** | strong | strong | excellent | excellent | excellent | medium | ✅ |

¹ Eve is single-user with **one audio channel** — two TTS answers can't play at
once. "Multiple simultaneous pending tasks" is a prioritized *stack/queue*, not
concurrent actors. An actor framework buys concurrency Eve doesn't have and
pays complexity for it.

---

## 4. Chosen architecture — hybrid

**Hierarchical state machine (control) + event-driven turn intake (integration)
+ a ConversationContext stack (nesting) + one structured response protocol
(feature integration).**

Each piece earns its place against a specific requirement:

- **HSM** — the "awaiting" states share one parent **`Engaged`** superstate that
  owns: listen-without-wake-word, honor timeout-extension phrases, allow cancel,
  apply the follow-up grace window. Written once, inherited by every child. A
  flat FSM would copy those edges onto each state.
- **Event intake** — audio utterance, silence timeout, TTS-finished, and later
  reminder-fires / device-callbacks are typed events into `engine.handle(event)`.
  This makes proactive conversations and interruptions additive, not a rewrite.
- **Context stack (pushdown)** — nested conversations: "set a timer" → "how
  long?" → *mid-clarification* "wait, what time is it?" → answer → resume the
  timer prompt. A stack of `ConversationContext` frames; the HSM runs on the top.
- **Structured response protocol** — features return typed `Outcome`s instead of
  bare strings. The engine turns them into states, prompts, and the follow-up
  decision. **This one protocol replaces `pending_confirm`, `converse`, and
  `Verified.on_fail`** — the de-fragmentation.

---

## 5. Core types (`core/conversation.py`)

### 5.1 States

```python
class State(Enum):
    IDLE                    # no active conversation; wake word required
    LISTENING               # mic open, capturing a user turn
    PROCESSING              # transcribing + routing
    EXECUTING               # a handler is running (maybe slow/async)
    AWAITING_CONFIRMATION   # yes/no ("are you sure?", "did you mean X?")
    AWAITING_CLARIFICATION  # choose among options ("upstairs or downstairs?")
    AWAITING_SLOT           # missing required info ("for how long?")
    RETRY_PENDING           # recoverable failure; offered try/skip/other
    FOLLOWUP_ACTIVE         # answered; grace window open for a continuation
    COMPLETED               # success sink → FOLLOWUP_ACTIVE or IDLE
    CANCELLED               # user cancelled → IDLE
    TIMED_OUT               # no input in window → IDLE
```

**`Engaged` superstate** = { LISTENING(follow-up), AWAITING_CONFIRMATION,
AWAITING_CLARIFICATION, AWAITING_SLOT, RETRY_PENDING, FOLLOWUP_ACTIVE }. In any
`Engaged` state: the mic re-opens without the wake word, extension phrases are
honored, and "cancel/never mind" ends the conversation gracefully.

### 5.2 ConversationContext (a stack frame)

```python
@dataclass
class ConversationContext:
    intent:        str | None            # resolved intent / handler id
    handler:       Callable | None       # runs once slots satisfied
    entities:      dict                  # extracted slot values
    missing_slots: list[str]             # slots still needed, in ask order
    slot_prompts:  dict[str, str]        # slot -> question
    clarify:       list[tuple[str, Any]] # pending (label, action) options
    clarify_history: list[str]           # questions already asked
    prompts:       list[str]             # assistant lines said this convo
    retry_count:   int
    confidences:   dict[str, float]      # per-candidate scores
    referents:     dict[str, Any]        # 'it'/'them'/'that' -> object(s)
    state:         State
    created:       float
    last_activity: float
    deadline:      float                 # extended by "hold on" etc.
    on_resume:     Callable | None       # run when a pushed child pops
```

This carries exactly the required conversation memory: pending intent,
entities, missing slots, clarification history, previous prompts, retry count,
confidences, referents for pronoun resolution.

### 5.3 Events (into `engine.handle(event)`)

```python
UserTurn(text)          # a transcribed utterance (wake-initiated or follow-up)
SilenceTimeout()        # listening window elapsed with no speech
SpeechFinished()        # TTS finished (may open a follow-up window)
Proactive(payload)      # a reminder fired / a system event wants to speak
Cancel()                # explicit barge-in / cancel
```

### 5.4 Outcomes (what handlers return; the engine interprets)

```python
Done(message, *, followup=True)                    # success; optional grace window
NeedConfirm(action, prompt, *, on_no=None)         # → AWAITING_CONFIRMATION
NeedClarify(prompt, options: list[(label, action)])# → AWAITING_CLARIFICATION
NeedSlot(name, prompt)                             # → AWAITING_SLOT
Failed(message, recovery: list[(label, action)])   # → RETRY_PENDING
Handoff(target, text)                              # escalate (e.g. LLM fallback)
```

Legacy handlers returning a bare `str` are auto-wrapped `Done(str)` during
migration, so features migrate incrementally.

---

## 6. State chart

```
                         wake word / Proactive
        ┌──────────────────────────────────────────────────────┐
        ▼                                                        │
 ┌──────────┐  audio    ┌────────────┐  text   ┌────────────┐   │
 │   IDLE   │──────────▶│ LISTENING  │────────▶│ PROCESSING │   │
 └──────────┘           └────────────┘         └─────┬──────┘   │
   ▲   ▲                     ▲                        │ route    │
   │   │                     │ follow-up (no wake)    ▼          │
   │   │              ┌──────┴───────┐         ┌────────────┐    │
   │   │              │   Engaged     │◀───────│ EXECUTING  │    │
   │   │              │ (superstate)  │ Outcome└─────┬──────┘    │
   │   │              │  ┌─────────┐  │              │           │
   │   │              │  │AWAIT_*  │  │  Done        ▼           │
   │   │              │  │RETRY    │  │        ┌────────────┐    │
   │   │              │  │FOLLOWUP │  │        │ COMPLETED  │────┘
   │   │              │  └─────────┘  │        └─────┬──────┘
   │   │              └──────┬───────┘  answer        │ followup=False
   │   │   timeout / cancel  │  resolves               ▼
   │   └─────────────────────┘                    (FOLLOWUP_ACTIVE
   │        TIMED_OUT / CANCELLED                   or IDLE)
   └───────────────────────────────────────────────────────────
```

### Transition table (essentials)

| From | Event / result | To | Side effect |
|------|----------------|----|-------------|
| IDLE | wake word | LISTENING | open mic |
| IDLE | Proactive | LISTENING/EXECUTING | speak, then follow-up window |
| LISTENING | UserTurn | PROCESSING | transcribe done → route |
| LISTENING | SilenceTimeout | TIMED_OUT→IDLE | (optional) "still there?" once |
| PROCESSING | Outcome=Done(followup) | FOLLOWUP_ACTIVE | speak; open no-wake window |
| PROCESSING | Outcome=NeedConfirm | AWAITING_CONFIRMATION | speak prompt; open window |
| PROCESSING | Outcome=NeedClarify | AWAITING_CLARIFICATION | speak options; open window |
| PROCESSING | Outcome=NeedSlot | AWAITING_SLOT | speak prompt; open window |
| PROCESSING | Outcome=Failed | RETRY_PENDING | speak + recovery menu; open window |
| any Engaged | extension phrase | (unchanged) | "take your time"; extend deadline |
| any Engaged | "cancel/never mind" | CANCELLED→IDLE | "okay, cancelled" |
| any Engaged | SilenceTimeout | TIMED_OUT→IDLE | drop context (or one nudge) |
| AWAITING_CONFIRMATION | yes | EXECUTING | run action |
| AWAITING_CONFIRMATION | no | COMPLETED/IDLE | run on_no or drop |
| AWAITING_CLARIFICATION | option match | EXECUTING | run chosen action |
| AWAITING_SLOT | slot value | PROCESSING | fill slot; re-check missing |
| RETRY_PENDING | "try again" | EXECUTING | re-run handler |
| RETRY_PENDING | "skip / another / last one" | EXECUTING | run chosen recovery |
| FOLLOWUP_ACTIVE | UserTurn | PROCESSING | route as continuation |
| FOLLOWUP_ACTIVE | SilenceTimeout | IDLE | quietly end |

---

## 7. Audio: the follow-up window (the linchpin)

`listener.py` grows a second entry point:

```python
def listen_followup(self, ttl: float) -> np.ndarray | None:
    """Open the mic for up to `ttl` seconds WITHOUT waiting for the wake word.
    Returns audio, or None on silence timeout. Same silence/echo gating as
    _record_command (respects is_speaking + the post-TTS cooldown)."""
```

`main.on_command` becomes engine-driven: after speaking any `Engaged`-leaving
Outcome (or `Done(followup=True)`), the engine asks the listener for a
follow-up window and feeds the transcript back as `UserTurn` — no wake word.

**Echo / barge-in safety (Phase 1, not deferred):**
- Keep the existing `is_speaking` gate + post-TTS cooldown so Eve's own voice
  can't fill the follow-up window.
- The follow-up window is bounded (`followup_ttl`, default ~8 s, configurable)
  and closes on first end-of-speech.
- A TV/room echo re-triggering the window is a known risk; Phase 1 mitigation
  is the cooldown + short window; true barge-in (interrupt TTS mid-sentence) is
  a later enhancement, explicitly out of Phase 1.

---

## 8. Timeout management & extension phrases

Handled on the `Engaged` superstate, **before** the utterance reaches feature
routing:

```python
_EXTEND = {"one moment", "hold on", "give me a second", "hang on", "wait",
           "just a minute", "let me check", "i'm thinking", "standby",
           "one sec", "hold up"}
```

Matching an extension phrase in any `Engaged` state → reply ("No problem." /
"Take your time.") + refresh `context.deadline` + **stay in the same state**.
The pending intent, slots, and clarification survive untouched. Works anywhere
Eve is awaiting input because it lives on the parent state, not per feature.

Deadlines: default follow-up/awaiting TTL configurable in settings
(`conversation.followup_ttl`, `conversation.awaiting_ttl`); extension phrases
add `conversation.extend_by`.

---

## 9. Error recovery

`Failed(message, recovery)` replaces terminal error strings. The engine speaks
the message plus a spoken menu and enters `RETRY_PENDING`:

> "I couldn't reach that device. Want me to try again, pick another, or skip it?"

`recovery` is a list of `(label, action)`; the engine matches the user's reply
against labels (fuzzy) and runs the chosen action, staying engaged. Standard
recovery verbs recognized everywhere: *try again, retry, skip, another / a
different one, the last one, continue anyway, cancel*.

Sources that produce `Failed` instead of ending:
- `Verified.on_fail` (app didn't close, window didn't move) → offer retry / kill.
- LLM fallback errors / timeouts → offer retry / rephrase.
- Device/API failures (future smart-home) → offer retry / another / last known.
- `main.on_command` exception handler → `Failed("something went wrong",
  [("try again", rerun)])` instead of a dead "something went wrong".

---

## 10. Conversation memory & pronoun resolution

`ConversationContext.referents` is populated by handlers:

```python
ctx.set_referent("it", window_hwnd)     # "close it", "move it"
ctx.set_referent("them", device_list)   # "turn them off"
```

A resolver expands pronouns ("it / that / those / them / the last one") against
the top-of-stack `referents` first, then `last_action`. This unifies today's
`last_action` ("cancel it", "go back") into the same memory model. Referents
carry across follow-up turns within a conversation and are dropped when the
conversation ends.

Conversational context ("what about tomorrow?", "what about the bedroom?") is
supported by keeping the previous turn's resolved intent + entities on the
context; a continuation re-runs the same handler with the delta applied.

---

## 11. Did-you-mean / disambiguation (unified)

All confidence-based clarification funnels through `NeedClarify`. Confidence
tiers (reuse `core/intent_match.py`):

- **HIGH** (≥ `HIGH_THRESHOLD`, strict-check passes) → execute silently.
- **MED** → `NeedClarify` / `NeedConfirm` ("Did you mean X?" / "X or Y?").
- **LOW** → no match → escalate (learned tier → LLM → recovery).

Signals feeding confidence: fuzzy score (rapidfuzz), aliases, **historical
usage** (frequency from `intent_training.json` / learned store), and per-source
scores. The three current disambiguation paths — `_guess_dispatch`,
`close_app` running-process suggestion, and (future) device selection — all
become `NeedClarify` producers. Examples:

> "Did you mean Kitchen Lights or Dining Room Lights?"
> "I found two bedrooms — upstairs or downstairs?"
> "Did you mean chrome?" (close_app)

---

## 12. Feature integration contract

A feature **never** manages conversation state directly. It returns an
`Outcome`; the engine does the rest. Examples:

**Slot filling (timer):**
```python
def set_timer(minutes=None):
    if minutes is None:
        return NeedSlot("minutes", "For how long?")
    return Done(f"Timer set for {minutes} minutes.")
# "set a timer" → NeedSlot → "For how long?" → "ten minutes" → engine refills
# the slot and re-invokes set_timer(10) → Done. No wake word between turns.
```

**Disambiguation (lights):**
```python
def lights_on(room):
    matches = find_rooms(room)
    if len(matches) > 1:
        return NeedClarify("Which one?",
                           [(m.name, lambda m=m: m.turn_on()) for m in matches])
    return Done(matches[0].turn_on())
```

**Recoverable failure (close app):**
```python
return Failed(f"{name} is still running — it may be waiting on you.",
              [("try again", lambda: close_app(name)),
               ("force it",  lambda: kill_app(name)),
               ("skip it",   lambda: Done("Okay, leaving it."))])
```

**Confirmation (destructive):**
```python
return NeedConfirm(lambda: really_shutdown(), "Shut down the PC — are you sure?")
```

This is the single reusable surface every feature leverages, replacing the
scattered `pending_confirm` / `start_converse` / `Verified` wiring.

---

## 13. Migration map (current → new home)

| Current mechanism | New home |
|-------------------|----------|
| `session.pending_confirm` + `_handle_confirmation` | `NeedConfirm` → `AWAITING_CONFIRMATION`; engine yes/no resolver |
| `_guess_dispatch` did-you-mean | `NeedClarify` (MED tier) |
| `close_app` running-process suggestion | `NeedClarify` |
| `Converse` + `start_converse`/`clear_converse` + `_handle_converse` | context frame on the stack; skills return `NeedSlot`/`Done`; `FOLLOWUP_ACTIVE` |
| `Mode` enum (LISTING/PLAYING/BROWSING) + scattered checks | `context.state` + skill-owned sub-context on the stack |
| `session.last_action` ("cancel it/go back") | `context.referents` + pronoun resolver |
| `Verified.on_fail` (terminal) | `Failed(recovery=[…])` → `RETRY_PENDING` |
| `Silent` prompts not spoken (`main.py:155`) | engine always speaks prompts; `Silent` reserved for truly silent HUD notes |
| `main.on_command` exception → dead end | `Failed` with a retry option |
| `session.reset()` (nukes everything) | `engine.end_conversation()` pops the stack cleanly |
| per-turn wake gating (`listener.run`) | `Engaged` states use `listen_followup()` — no wake word |

`video_list`/`site_list`/`selected_*` stay as list-selection data but move onto
the active context frame rather than the global session.

---

## 14. Module layout

```
core/conversation.py     # ConversationEngine, ConversationContext, State,
                         # Event types, Outcome types, extension-phrase matcher,
                         # pronoun resolver, timeout policy. Pure logic.
core/listener.py         # + listen_followup(ttl)  (no-wake window)
main.py                  # on_command becomes engine.handle(UserTurn(...));
                         # the speak/render loop reads engine directives
core/dispatcher.py       # dispatch() returns Outcomes (or str auto-wrapped);
                         # confirm/converse/guess routing removed once migrated
core/session.py          # slimmed: engine owns conversation state; session keeps
                         # only cross-conversation prefs if any
```

The engine is **pure logic with no audio/IO dependencies** — it takes Events and
returns directives (speak this, listen for `ttl`, run this handler). That makes
it fully unit-testable without a microphone.

---

## 15. Testing strategy

`tests/test_conversation.py` (engine is pure → no audio needed). Cases:
- clarification flow (ambiguous → choose → execute, no wake word between)
- slot filling (missing → prompt → fill → execute; multi-slot in sequence)
- confirmation yes / no / unrelated-utterance
- retry: fail → "try again" → succeed; fail → "skip"
- recovery menu matching (fuzzy label match, unknown reply re-prompts once)
- timeout: awaiting → silence → TIMED_OUT → IDLE
- **extension phrases**: each of "hold on / one moment / I'm thinking / …" in
  every Engaged state extends the deadline and preserves pending intent/slots
- multi-turn follow-ups ("turn off the TV" → "actually turn it back on")
- pronoun resolution ("it / them / that / the last one")
- nested conversation (push child mid-slot-fill, resolve, resume parent)
- interruption / cancel mid-clarification
- speech-recognition error (empty/garbled transcript) → re-prompt, stay engaged
- API/device failure → `Failed` → recovery, conversation stays alive
- successful recovery after failure end-to-end

---

## 16. Rollout (phased, feature-flagged)

Gated behind `features.json` `conversation_engine` (default off until proven);
the current path stays the default and runnable side-by-side.

1. **Engine skeleton + follow-up window** — HSM, `ConversationContext`, events,
   `listen_followup`; wire confirmation + did-you-mean first.
2. **Structured Outcome protocol** — migrate confirmation/disambiguation; delete
   `pending_confirm`.
3. **Extension phrases** on the `Engaged` superstate.
4. **Error recovery** — `Failed`/`RETRY_PENDING` for `Verified.on_fail`, LLM
   errors, the exception path.
5. **Migrate `converse`/`Mode`/YouTube** and slot-filling.
6. **Tests** (§15) comprehensive.
7. **Update this doc** to match the shipped API + a short "how to add a
   conversational feature" recipe.

---

## 17. Risks & open questions

- **Blast radius:** rewrites the audio loop and every feature's return
  convention. Mitigation: feature flag, keep the old path, migrate incrementally.
- **Echo / false follow-up:** a TV or Eve's own voice filling the no-wake
  window. Mitigation: `is_speaking` gate + post-TTS cooldown + bounded window;
  true barge-in deferred.
- **Follow-up window length:** too short feels clipped, too long feels like Eve
  is "listening to everything." Make it configurable; default ~8 s; consider a
  subtle HUD "listening" cue so the user knows the window is open.
- **Proactive speech vs. focus invariant:** proactive conversations must still
  respect the game-focus rules (never steal focus). Proactive prompts are
  voice-only when a protected app is foreground.
- **Nested-context depth:** cap the stack depth to avoid runaway nesting.
