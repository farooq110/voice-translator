MASTER PROMPT — AI Real-Time Voice Translator

1. Project Overview

Build a reliable, real-time AI-powered voice translator that allows two people speaking different languages to communicate naturally through browser-based conversations and, eventually, meeting applications.

The system must capture speech, detect the spoken language, determine whether translation is required, translate when necessary, generate translated speech, and deliver the correct audio to the other participant.

The most important product requirement is that only the person using the translator configures their language preferences. The other participant does not need to install, configure, or operate the translator.

The system must work according to the translator user's language settings, not fixed Person A / Person B language assignments.

---

2. Core Language Configuration

When a user enables the translator, ask them to configure two settings:

1. Native Language: The language the translator user naturally speaks and wants to receive translations in.
2. Preferred Translation Language: The language the translator user wants the other person to hear when they speak their native language.

Example:

- Native Language: Urdu
- Preferred Translation Language: English

Only the translator user needs to configure these settings.

The other participant must not be required to configure their native language or preferred translation language.

The system must automatically detect the language spoken by either participant.

Important

Do not create mandatory language settings for Person A and Person B.

Do not assume:

- Person A always speaks Urdu.
- Person B always speaks English.
- Both participants must configure the translator.
- Every incoming speech segment requires translation.

The translator user's configuration controls the translation behavior.

---

3. Mandatory Translation Logic

The following language rules define the core behavior of the entire product.

Assume the translator user has configured:

- Native Language: Urdu
- Preferred Translation Language: English

Rule 1 — Translator User Speaks Native Language

When the translator user speaks Urdu:

1. Capture the original speech.
2. Detect Urdu as the source language.
3. Transcribe the speech.
4. Translate Urdu into English.
5. Generate English speech using TTS.
6. Deliver the translated English audio to the other participant.

The translator user must not receive an unnecessary translation of their own speech.

Rule 2 — Other Participant Speaks Preferred Translation Language

When the other participant speaks English:

1. Capture the original speech.
2. Detect English as the source language.
3. Transcribe the speech.
4. Translate English into Urdu.
5. Generate Urdu speech using TTS.
6. Deliver the translated Urdu audio to the translator user.

The other participant must not receive an unnecessary translation of their own speech.

Rule 3 — Any Other Language

If either participant speaks a language other than the configured Native Language and Preferred Translation Language, the system must NOT translate it.

For example, if the configured languages are Urdu and English:

- Arabic must pass through unchanged.
- Spanish must pass through unchanged.
- French must pass through unchanged.
- Any other unsupported or unconfigured language must pass through unchanged.

Expected behavior:

1. Detect the spoken language where possible.
2. Do not call the translation provider.
3. Do not call TTS.
4. Forward the original audio directly to both participants.
5. Preserve the original spoken language.

Rule 4 — Original Audio Pass-Through

Whenever translation is not required:

- Forward the original audio.
- Do not regenerate the speech using TTS.
- Do not unnecessarily modify the spoken content.
- Avoid unnecessary STT, translation, and TTS processing.
- Maintain the original audio quality as much as the transport format allows.

If the audio needs format conversion for transport, preserve its original spoken content and do not alter its language.

Expected Behavior Table

Speaker| Spoken Language| Translator User Receives| Other Participant Receives
Translator User| Urdu| Original Urdu| Translated English
Other Participant| English| Translated Urdu| Original English
Either participant| Arabic| Original Arabic| Original Arabic
Either participant| Spanish| Original Spanish| Original Spanish
Either participant| French| Original French| Original French

This table is based on the example configuration of Urdu as Native Language and English as Preferred Translation Language.

General Rule

The translation engine must dynamically apply these rules to any supported configured language pair.

Do not hardcode Urdu and English into the core business logic.

---

4. Important Language Detection and Routing Requirements

Build a dedicated translation decision engine.

For each incoming speech segment, determine:

1. Which audio stream produced the speech?
2. Who is speaking?
3. What language was detected?
4. Is the detected language the translator user's Native Language?
5. Is the detected language the translator user's Preferred Translation Language?
6. Is it another language?
7. Is translation required?
8. Who should receive the original or translated audio?

Translation Decision Logic

Use the following conceptual behavior:

IF detected_language == native_language:
    Translate into preferred_translation_language
    Send translated audio to the other participant

ELSE IF detected_language == preferred_translation_language:
    Translate into native_language
    Send translated audio to the translator user

ELSE:
    Do not translate
    Forward original audio to both participants

This logic must be implemented carefully with the actual speaker and recipient identity.

The system must not translate a participant's speech back to that same participant unnecessarily.

Language Detection Reliability

- Use automatic language detection.
- Handle short speech segments where detection confidence may be low.
- Avoid incorrect language switching.
- Maintain language detection context across related segments where appropriate.
- Use confidence thresholds.
- When detection is uncertain, do not generate an incorrect translation.
- Use original audio pass-through when the language cannot be confidently classified.
- Do not silently classify an unknown language as Urdu or English.

---

5. Development Strategy

Build the product incrementally.

Do not implement all meeting integrations at once.

Development order:

1. Browser-based voice translation MVP.
2. Mobile browser compatibility.
3. Zoom integration.
4. Additional meeting application integrations.
5. Production readiness and release.

The browser MVP must work before starting meeting integrations.

Do not start with Electron, a desktop application, or a native mobile application.

Do not build speculative integrations before they are needed.

---

6. Supported Platforms

The first application must be responsive and mobile-first.

Target platforms:

- Android browsers.
- iOS browsers.
- Desktop browsers.
- Tablets.

Initial browser testing:

- Android Chrome.
- Desktop Chrome.
- iOS Safari.

Use browser APIs:

- MediaDevices API.
- getUserMedia().
- Web Audio API where appropriate.
- WebSocket for real-time communication.

Handle:

- Microphone permissions.
- Audio autoplay restrictions.
- Microphone interruptions.
- Network interruptions.
- Device changes.
- Audio playback failures.
- Background tab limitations.
- Screen-lock limitations.
- Battery and resource usage.
- Mobile touch interactions.

Do not claim background operation or locked-screen functionality works unless it has been tested on real devices.

Clearly document platform-specific limitations.

---

7. Preferred Technology Stack

Frontend

- React.
- TypeScript.
- Vite.
- Browser audio APIs.
- WebSocket client.
- Responsive UI.

Backend

- Python.
- FastAPI.
- uv for dependency management.
- WebSocket endpoints.
- Local CPU-based speech recognition.

Communication

Use WebSocket for real-time audio streaming and translation events.

Define a clear, versioned audio communication protocol.

Engineering Requirements

- Strict TypeScript.
- Avoid unnecessary dependencies.
- Avoid "any" types.
- Follow SOLID principles where appropriate.
- Keep modules loosely coupled.
- Separate business logic from UI.
- Use dependency injection where it improves testability.
- Centralize configuration.
- Use environment variables for secrets and configuration.
- Maintain dependency lockfiles.
- Prefer simple, maintainable architecture over unnecessary abstractions.

If an existing repository is available, inspect and reuse its architecture where reasonable.

Do not rebuild an existing project without a clear reason.

---

8. Speech-to-Text (STT)

Speech recognition must run on the backend.

The default STT approach must use a local CPU-compatible model.

The application must not require a GPU.

Evaluate Whisper.cpp or another suitable CPU-compatible solution.

Test actual recognition quality for:

- Urdu.
- English.
- Other languages supported by the selected model.

Requirements:

1. Capture microphone audio in the browser.
2. Send audio to the backend.
3. Process audio using the selected STT model.
4. Detect the spoken language.
5. Generate transcription.
6. Return transcription and language metadata.
7. Handle recognition failures.
8. Avoid blocking the backend event loop with CPU-heavy work.

Evaluate actual CPU usage, memory consumption, recognition accuracy, and latency.

Do not assume a model performs well without testing it against real audio samples.

---

9. Voice Activity Detection and Audio Processing

Implement appropriate audio processing.

Requirements:

- Voice Activity Detection (VAD).
- Silence detection.
- Audio segmentation.
- Noise handling where appropriate.
- Audio format validation.
- Bounded queues and buffers.
- Backpressure handling.
- Prevention of unbounded memory growth.
- Cancellation of outdated processing tasks.
- Prevention of stale audio playback.

Avoid sending unnecessary silence to the STT system.

Do not introduce excessive segmentation that damages recognition accuracy.

Measure the effect of audio processing on latency and accuracy.

---

10. Translation Engine

Build a reusable translation engine.

The translation engine must support dynamic language configurations.

Requirements:

- Automatic source-language detection.
- Native-language configuration.
- Preferred translation-language configuration.
- Translation decision logic.
- Translation provider abstraction.
- Unicode support.
- Urdu right-to-left handling.
- Error handling.
- Request timeouts.
- Rate limits.
- Retry policies where appropriate.
- Cancellation of outdated requests.
- Session-aware processing.

The application does not need to be fully offline.

Cloud translation providers are allowed.

Keep provider implementations modular so providers can be changed without rewriting the translation engine.

Do not expose API keys in the frontend.

Translation Optimization

When translation is not required, avoid unnecessary processing.

For pass-through audio:

- Do not call the translation provider.
- Do not call TTS.
- Avoid unnecessary STT where the system can safely determine that translation is not needed.

However, language detection must be reliable enough to prevent incorrect translation decisions.

---

11. Text-to-Speech (TTS)

The application must generate translated speech only when translation is required.

Evaluate suitable TTS providers for:

- Urdu voice quality.
- English voice quality.
- Additional language support.
- Latency.
- Cost.
- API reliability.
- Licensing.
- Privacy.
- Voice naturalness.

Cloud TTS services are permitted.

Requirements:

1. Generate audio in the required target language.
2. Return audio in a browser-compatible format.
3. Support real-time or near-real-time playback.
4. Handle TTS failures.
5. Prevent overlapping audio.
6. Cancel outdated audio.
7. Avoid playing translations from outdated language settings.
8. Keep TTS providers replaceable.

Do not regenerate original pass-through speech using TTS.

Do not claim high-quality Urdu TTS until it has been tested with real examples.

---

12. Browser Audio Playback

For the initial browser MVP, translated speech must play locally in the appropriate browser.

Distinguish between:

- Original audio pass-through.
- Translated audio playback.
- Audio sent into a meeting application's microphone input.

These are different capabilities.

The initial MVP needs reliable browser-based audio delivery.

Handle autoplay restrictions, playback interruptions, device routing, and audio cleanup.

Do not claim that local playback automatically makes translated audio audible to another person inside Zoom or another meeting application.

---

13. Core Architecture

Keep the translation engine independent from meeting application integrations.

Suggested architecture:

A. Frontend

- Microphone capture.
- Native language selection.
- Preferred translation language selection.
- Session controls.
- Audio streaming.
- Transcription display.
- Translation display.
- Audio playback.
- Connection status.
- Error handling.

Only the translator user configures their language preferences.

B. Backend Core

- Session management.
- Audio ingestion.
- Audio validation.
- VAD.
- Source-language detection.
- Speech-to-Text.
- Translation decision engine.
- Translation.
- Text-to-Speech.
- Original audio pass-through.
- Audio conversion.
- Audio routing.
- Cancellation and cleanup.

C. Translation Decision Engine

This module must independently decide whether to:

- Translate Native Language speech into Preferred Translation Language.
- Translate Preferred Translation Language speech into Native Language.
- Pass through any other language without translation.

Keep this logic separate and unit-testable.

D. Audio Routing

The audio routing layer must:

- Identify the source participant.
- Identify the receiving participant.
- Determine whether translation is required.
- Send original audio or translated audio to the correct destination.
- Prevent audio loops.
- Prevent sending translated speech back to its original speaker unnecessarily.

E. Provider Layer

Keep separate replaceable interfaces for:

- STT.
- Language detection if separate from STT.
- Translation.
- TTS.

F. Meeting Integration Layer

Implement later.

It may include:

- Meeting audio capture.
- Participant identification.
- Audio routing.
- Virtual audio devices.
- Meeting-specific adapters.
- Translated microphone output.

Do not overengineer this layer during the initial browser MVP.

---

14. Meeting Application Integration

Long-term target applications:

- Zoom.
- WhatsApp.
- Discord.
- Google Meet.
- Microsoft Teams.
- Other compatible communication applications.

Implement integrations one at a time.

Zoom must be the first meeting integration.

Important Technical Distinction

There are three separate capabilities:

1. Capture the user's microphone audio.
2. Capture remote participants' meeting audio.
3. Send translated audio into the meeting as microphone input.

Implementing browser microphone capture does not automatically provide the other two capabilities.

For Zoom, investigate:

- Available audio capture mechanisms.
- System audio access.
- Supported APIs and SDKs.
- Virtual audio devices.
- Audio routing.
- Operating system compatibility.
- Microphone input injection.
- Feedback-loop prevention.

Do not assume a Chrome extension alone can capture all system audio or create a system-wide virtual microphone.

Avoid audio feedback loops.

Start with two-person conversations.

Only introduce multi-participant speaker identification and routing when it can be verified.

Determine the target operating system before making OS-specific implementation decisions.

Do not claim universal Zoom support until the complete integration has been tested.

The configured language behavior must remain consistent across all future meeting integrations.

---

15. Performance and Reliability

Prioritize a practical balance between:

- Translation accuracy.
- End-to-end latency.
- CPU consumption.
- Memory consumption.
- Network usage.
- Provider cost.
- Audio quality.

Measure actual performance.

Track:

- Audio capture time.
- Audio upload time.
- VAD processing time.
- STT processing time.
- Translation time.
- TTS generation time.
- Audio delivery time.
- Total end-to-end latency.
- CPU and memory consumption.

Implement:

- Bounded queues.
- Backpressure.
- Timeouts.
- Cancellation.
- Session cleanup.
- Network reconnection.
- Stale-result rejection.
- Error recovery.

Do not allow overloaded sessions to accumulate unlimited audio or translation tasks.

---

16. Security and Privacy

Requirements:

- Never hardcode API keys.
- Keep secrets on the backend.
- Validate audio formats and payload sizes.
- Secure WebSocket connections.
- Configure appropriate CORS and origin restrictions.
- Apply rate limits.
- Use request timeouts.
- Avoid unnecessary audio retention.
- Avoid logging sensitive audio or transcripts unnecessarily.
- Clean up session resources.
- Document which external providers receive audio or text.
- Document data retention behavior.

Do not make unsupported claims about encryption, privacy, or provider data handling.

---

17. DEVELOPMENT ROADMAP — 10 PHASES

Work through these phases sequentially.

Do not skip phases.

Do not start a future phase without explicit approval.

Phase 1 — Project Foundation and Browser STT MVP

Inspect the existing repository first.

Implement:

- React + TypeScript + Vite frontend.
- FastAPI backend.
- uv dependency management.
- Health-check endpoint.
- Responsive browser UI.
- Native language selection.
- Preferred translation language selection.
- Microphone permission handling.
- Microphone capture.
- Audio transfer to backend.
- WebSocket communication protocol.
- Local CPU-based STT.
- Urdu and English speech recognition.
- Automatic source-language detection.
- Transcription display.
- Basic session handling.
- Connection and error states.
- Initial tests.
- Architecture and setup documentation.

Do not implement translation or TTS yet.

Acceptance Criteria:

- Browser captures real microphone audio.
- Backend receives real audio.
- Local CPU STT processes it.
- Actual Urdu and English speech can be transcribed.
- Language detection works to the extent supported and is honestly documented.
- User can configure both language preferences.
- Errors and connection failures are handled.
- Application runs without requiring a GPU.
- Tests are executed and results documented.

Phase 2 — Voice Activity Detection and Audio Optimization

Implement:

- VAD.
- Silence detection.
- Audio segmentation.
- Efficient audio transfer.
- Bounded buffers and queues.
- Backpressure.
- Cancellation.
- Memory-growth prevention.
- CPU and memory measurements.

Test with real audio.

Document measurable improvements and accuracy trade-offs.

Phase 3 — Translation Decision Engine and Bidirectional Translation

Implement:

- Translation provider abstraction.
- Automatic source-language detection.
- Native Language configuration.
- Preferred Translation Language configuration.
- Translation decision engine.
- Urdu → English.
- English → Urdu.
- Additional supported language pairs.
- Unicode handling.
- Urdu RTL support.
- Translation timeouts.
- Error handling.
- Rate limiting.
- Cancellation of stale tasks.

Implement the exact language rules:

1. Native Language speech from translator user → translate into Preferred Translation Language for the other participant.
2. Preferred Translation Language speech from the other participant → translate into Native Language for the translator user.
3. Any other language → original audio pass-through to both participants without translation.

Do not create fixed Person A / Person B language assignments.

Test all language decision scenarios using real speech and provider responses.

Phase 4 — Text-to-Speech and Browser Audio Playback

Implement:

- TTS provider abstraction.
- Urdu TTS evaluation and integration.
- English TTS evaluation and integration.
- Target-language speech generation.
- Browser-compatible audio output.
- Audio playback.
- Playback queue management.
- Prevention of overlapping audio.
- Cancellation of outdated audio.
- Provider failure handling.

Original audio pass-through must bypass TTS.

Test actual generated audio.

Phase 5 — Complete Browser MVP

Complete the pipeline:

Microphone → VAD → STT → Language Detection → Translation Decision → Translation (when required) → TTS (when required) → Audio Routing → Browser Playback

Requirements:

- Two-person browser sessions.
- Only translator user configures language preferences.
- Automatic source-language detection.
- Correct translation decision.
- Correct original audio pass-through.
- Correct translated audio delivery.
- Session creation and cleanup.
- Cancellation of outdated processing.
- Network reconnection.
- Error recovery.
- Latency measurements.

Test these scenarios:

1. Translator user speaks their Native Language.
2. Other participant speaks the Preferred Translation Language.
3. Translator user speaks a third language.
4. Other participant speaks a third language.
5. Both participants speak the same language.
6. Language detection confidence is low.
7. Unsupported language is detected.
8. Network interruption occurs during translation.
9. STT, translation, or TTS provider fails.
10. Original audio pass-through works without TTS.
11. Translated audio reaches only the intended recipient.
12. No feedback loop occurs.

This phase completes the first functional browser MVP.

Phase 6 — Mobile and Browser Compatibility

Test:

- Android Chrome.
- Desktop Chrome.
- iOS Safari.

Verify:

- Microphone permissions.
- Audio capture.
- Audio playback.
- WebSocket connectivity.
- Reconnection.
- Device changes.
- Interruptions.
- Background behavior.
- Screen-lock behavior.
- Responsive layout.
- Battery and resource usage.

Fix platform-specific problems.

Do not claim compatibility for devices or browsers that have not been tested.

Document known limitations.

Phase 7 — First Meeting Integration: Zoom

Only begin after approval of previous phases.

Investigate and implement Zoom integration.

Requirements:

- Capture meeting audio.
- Identify relevant speaker/audio source where supported.
- Route audio to the translation engine.
- Apply the translator user's language settings.
- Generate translated speech when required.
- Pass through original speech for other languages.
- Deliver translated or original audio to the correct participants.
- Prevent feedback loops.
- Handle meeting disconnections.
- Handle device changes.
- Verify real bidirectional conversations.

Investigate target OS and audio-routing mechanisms before implementation.

Use actual Zoom meetings for testing.

Do not claim success based only on mocked audio or simulated meeting sessions.

Phase 8 — Chrome Extension and Advanced Audio Integration

Evaluate whether a Chrome extension is useful.

Where appropriate, implement Manifest V3.

Investigate:

- Permissions.
- Browser tab audio capture.
- Backend communication.
- Extension lifecycle.
- Audio routing.
- Native messaging if required.
- System audio limitations.
- Virtual audio device requirements.

Do not assume an extension alone can capture all system audio or create a system-wide virtual microphone.

Phase 9 — Additional Meeting Applications

Add one at a time:

1. Google Meet.
2. Discord.
3. WhatsApp.
4. Microsoft Teams.
5. Other compatible applications.

For every application:

- Investigate supported integration mechanisms.
- Create an application-specific adapter.
- Reuse the core translation engine.
- Preserve the translator user's language configuration.
- Implement audio capture and delivery.
- Test real bidirectional conversations.
- Verify original audio pass-through.
- Verify translation decision logic.
- Verify feedback-loop prevention.
- Document limitations.
- Obtain approval before moving to the next application.

Do not claim support until the actual integration has been verified.

Phase 10 — Production Readiness, Packaging, and Release

Prepare the application for production.

Implement and verify:

- Production backend deployment.
- Frontend deployment.
- Environment configuration.
- Secret management.
- Provider configuration.
- Model download and setup.
- Dependency installation.
- Logging and monitoring.
- Resource management.
- Security checks.
- Error reporting.
- Supported browser and OS documentation.
- Troubleshooting documentation.
- Installation and deployment instructions.
- Production testing.
- Release checklist.

Document actual supported functionality and known limitations.

---

18. TESTING REQUIREMENTS

Testing is mandatory from Phase 1.

Use appropriate:

- Unit tests.
- Integration tests.
- End-to-end tests.
- Browser tests.
- Real-device tests.
- Real audio tests.
- Real provider tests.
- Meeting application tests.

Mocks may be used for isolated unit tests.

However, mocked tests must never be used to claim that real microphone capture, STT, translation, TTS, or meeting audio routing works.

For every important feature:

1. Reproduce the behavior.
2. Identify the root cause of failures.
3. Implement the smallest appropriate fix.
4. Add regression coverage.
5. Run relevant tests again.
6. Document actual results.

Never claim a test passed if it was not executed.

Never claim 100% test coverage unless it has actually been measured.

---

19. BUG FIXING AND CODE QUALITY

When a bug is reported:

- Reproduce it.
- Inspect logs and relevant code.
- Find the root cause.
- Implement a focused fix.
- Add or update regression tests.
- Run relevant tests.
- Verify the original issue is resolved.
- Document the result.

Do not ignore bugs.

Do not rewrite unrelated modules unnecessarily.

Do not mark incomplete features as complete.

Maintain readable, reusable, loosely coupled code.

---

20. DOCUMENTATION AND PROJECT STATE

Maintain project documentation throughout development.

Adapt this structure to the existing repository. Do not create duplicate files if equivalent documentation already exists.

project-root/
├── prompt.md
├── AGENT.md
├── README.md
├── docs/
│   ├── architecture.md
│   ├── audio-protocol.md
│   ├── testing.md
│   ├── deployment.md
│   └── decisions/
└── project-state/
    ├── CURRENT_STATE.md
    ├── TASKS.md
    ├── DECISIONS.md
    ├── KNOWN_ISSUES.md
    └── CHANGELOG.md

prompt.md

Complete project requirements, technical decisions, roadmap, and acceptance criteria.

AGENT.md

Permanent instructions for AI coding agents, including development rules, architecture constraints, testing, phase boundaries, approval requirements, documentation, and recovery instructions.

CURRENT_STATE.md

Record:

- Current phase.
- Current task.
- Completed work.
- Verified work.
- Actual test results.
- Known issues.
- Blockers.
- Next task.
- Whether phase approval is required.

TASKS.md

Track tasks using:

- TODO.
- IN PROGRESS.
- BLOCKED.
- DONE.

A task can only be marked DONE after implementation and verification.

DECISIONS.md

Record important technical decisions and reasoning.

KNOWN_ISSUES.md

Record issue description, reproduction steps, root cause, fix, verification, and current status.

CHANGELOG.md

Record meaningful project changes.

Update relevant documentation after every meaningful task and at the end of each phase.

Never claim documentation was updated unless it was actually edited.

---

21. NEW CHAT AND PROJECT RECOVERY

When starting a new conversation or resuming work:

1. Read AGENT.md.
2. Read prompt.md.
3. Read CURRENT_STATE.md.
4. Read TASKS.md.
5. Read DECISIONS.md.
6. Read KNOWN_ISSUES.md.
7. Inspect current Git status.
8. Inspect relevant source code.
9. Run relevant tests where appropriate.
10. Compare documentation with actual implementation.
11. Identify discrepancies.
12. Determine the earliest incomplete task in the currently approved phase.
13. Continue from that task.

Do not restart the project.

Do not redo verified work without a technical reason.

Do not assume documentation is accurate without checking the implementation.

If required repository files are inaccessible, request access or the relevant files. Never pretend to have inspected unavailable files.

---

22. GIT AND CHANGE MANAGEMENT

Before making changes:

- Inspect Git status.
- Identify existing user modifications.
- Preserve unrelated work.
- Avoid destructive commands.
- Do not overwrite user changes.
- Keep changes focused.
- Use meaningful checkpoints where appropriate.

Never run destructive Git operations without explicit permission.

Do not claim a commit was created unless it actually exists.

---

23. MANDATORY PHASE-BY-PHASE APPROVAL WORKFLOW

This is a strict development rule.

Work only on the currently approved phase.

Do not automatically proceed to the next phase.

Task Workflow

Break each phase into small, manageable tasks.

For each task, follow:

IMPLEMENT → TEST → FIX → VERIFY → DOCUMENT → REPORT

Do not start the next task while the current task has unresolved blocking problems.

Phase Completion Workflow

At the end of each phase, follow:

COMPLETE → TEST → FIX → VERIFY ACCEPTANCE CRITERIA → DOCUMENT → REPORT → REQUEST APPROVAL

Provide a clear phase completion report containing:

1. Features implemented.
2. Files created or modified.
3. Actual tests executed.
4. Test results.
5. Bugs discovered and fixed.
6. Unresolved issues.
7. Known limitations.
8. Acceptance criteria status.
9. Documentation updated.
10. Recommended next phase.

Then stop and ask:

"The current phase has been completed and reviewed. Do you approve starting the next phase?"

Wait for explicit approval.

Do not interpret silence, a status request, a question, or general discussion as approval.

If the user reports a problem, remain in the current phase, investigate it, fix it, and retest before requesting approval again.

If a blocking issue remains unresolved, do not declare the phase complete.

A new chat does not automatically authorize the next phase.

---

24. FIRST ACTION

Start by inspecting the existing repository, if available.

Do not immediately generate the entire application.

First:

1. Inspect repository structure.
2. Identify existing frontend and backend code.
3. Identify existing dependencies and architecture.
4. Identify existing tests.
5. Check current Git status.
6. Identify reusable components and existing functionality.
7. Compare the repository with these requirements.
8. Report current state and important gaps.
9. Prepare a Phase 1 task breakdown.
10. Begin only the first approved Phase 1 task.

Do not implement Phase 2 or any later phase.

---

25. FINAL NON-NEGOTIABLE REQUIREMENTS

- Start with a responsive, browser-based, mobile-first MVP.
- Do not start with Electron or a native mobile application.
- Do not require a GPU for the default local STT implementation.
- Only the translator user configures Native Language and Preferred Translation Language.
- The other participant does not need to configure the translator.
- Never hardcode Person A or Person B language assignments.
- Automatically detect the language spoken by either participant.
- Translate the translator user's Native Language into their Preferred Translation Language for the other participant.
- Translate the other participant's Preferred Translation Language into the translator user's Native Language.
- Any other language must pass through as original audio to both participants without translation.
- Do not regenerate original pass-through speech using TTS.
- Never translate unnecessarily.
- Route audio to the correct participants.
- Prevent audio feedback loops.
- Keep the translation engine reusable.
- Keep meeting-specific integrations separate.
- Implement Zoom only after browser MVP and mobile compatibility are verified.
- Add other meeting applications one at a time.
- Do not overengineer the initial MVP.
- Prioritize correctness, reliability, maintainability, and measurable performance.
- Always test real functionality.
- Never hide unresolved bugs.
- Never claim unverified functionality works.
- Never claim tests were run when they were not.
- Keep project documentation and recovery state updated.
- Never start the next phase without explicit user approval.

ULTIMATE PRODUCT GOAL

Build a practical, reliable, real-time AI voice translator where the translator user selects their Native Language and Preferred Translation Language only once. The system automatically detects both participants' spoken languages, translates only the configured language pair in the required direction, and forwards all other languages as original audio without unnecessary translation.