
import os
import hashlib
import wave
import array
import math
import gradio as gr
from groq import Groq
from sms_learning import WELCOME, initial_state, teach

api_key = os.environ.get("GROQ_API_KEY")
if not api_key or not api_key.strip():
    raise RuntimeError("Set the GROQ_API_KEY environment variable before starting the chatbot.")

# Connect to Groq API
client = Groq(
    api_key=api_key
)

def reply(message, history, learning_state):
    if isinstance(message, dict):
        if message.get("files"):
            raise gr.Error("Please wait for dictation to appear as text before sending.")
        message = message.get("text", "")
    if not message or not message.strip():
        raise gr.Error("Please enter an answer or choose a lesson number.")
    try:
        return teach(client, message, history, learning_state)
    except Exception:
        raise gr.Error("The answer could not be checked right now. Please retry; your learning progress has not changed.") from None


def transcribe(audio_path, draft, language="English"):
    draft = draft or ""
    if not audio_path:
        gr.Warning("Record your voice first, stop recording, then click Use dictation.")
        return draft
    # The recorder exports PCM WAV; reject silent input before transcription.
    try:
        with wave.open(audio_path, "rb") as audio:
            if audio.getsampwidth() == 2:
                samples = array.array("h", audio.readframes(audio.getnframes()))
                rms = math.sqrt(sum(value * value for value in samples) / max(len(samples), 1)) / 32768
                if rms < 0.0005:
                    gr.Warning("The recording is silent or too quiet. Check your microphone and record again. Your draft was kept.")
                    return draft
    except (wave.Error, EOFError):
        pass
    try:
        with open(audio_path, "rb") as recording:
            result = client.audio.transcriptions.create(
                file=recording, model="whisper-large-v3", response_format="verbose_json",
                timeout=30,
                language="en" if language == "English" else None,
                temperature=0,
            )
    except Exception as error:
        # Report only the error category, never provider text or credentials.
        category = type(error).__name__
        status = getattr(error, "status_code", None)
        print("Voice transcription failed:", category, status if isinstance(status, int) else "")
        guidance = {
            "AuthenticationError": "The Groq credential needs checking in the environment.",
            "RateLimitError": "Groq is temporarily rate-limiting transcription; wait briefly and record again.",
            "APIConnectionError": "The transcription service could not be reached; check your connection.",
            "APITimeoutError": "Transcription timed out; try a shorter recording.",
        }.get(category, "Please record again or type your message.")
        gr.Warning("Voice transcription could not complete (" + category + "). " + guidance + " Your draft was kept.")
        return draft
    segments = result.segments or []
    speech = [segment for segment in segments
              if segment.get("no_speech_prob", 1) < 0.6
              and segment.get("avg_logprob", -10) > -1.0]
    text = " ".join(segment.get("text", "").strip() for segment in speech).strip()
    if not text:
        gr.Warning("No clear speech detected. Check the selected microphone, speak closer, and record again. Your draft was kept.")
        return draft
    return " ".join(part for part in (draft.strip(), text) if part)


def apply_dictation(audio_path, draft, language, applied_recording):
    if not audio_path:
        transcribe(audio_path, draft, language)
        return gr.skip(), applied_recording, gr.update(interactive=False)
    try:
        with open(audio_path, "rb") as recording:
            recording_id = hashlib.sha256(recording.read()).hexdigest()
    except OSError:
        gr.Warning("The recording is no longer available. Record again; your draft was kept.")
        return gr.skip(), applied_recording, gr.update(interactive=True)
    if recording_id == applied_recording:
        # Do not overwrite any edits made since the first application.
        return gr.skip(), applied_recording, gr.update(interactive=False)
    updated = transcribe(audio_path, draft, language)
    if updated == (draft or ""):
        return gr.skip(), applied_recording, gr.update(interactive=True)
    return updated, recording_id, gr.update(interactive=False)


def dictation_to_draft(message, applied_recording):
    files = message.get("files", []) if message else []
    if not files:
        return gr.skip(), applied_recording
    draft = message.get("text", "") or ""
    text, applied_recording, _ = apply_dictation(files[-1], draft, "English", applied_recording)
    if isinstance(text, dict):
        text = draft
    return {"text": text, "files": []}, applied_recording


def validate_message(message):
    if isinstance(message, dict) and message.get("files"):
        raise gr.Error("Dictation is still being converted to text. Wait for the words to appear, then press Send.")


DICTATION_GUARD_JS = """
() => {
    const app = document.querySelector('gradio-app');
    const root = app?.shadowRoot || document;
    function sync() {
        const box = root.querySelector('#message-box');
        if (!box) return;
        const recording = !!box.querySelector('button[aria-label="Stop recording"]');
        const attach = box.querySelector('button[aria-label="Attach audio"]');
        if (attach && !attach.dataset.autoAttached) {
            attach.dataset.autoAttached = 'true';
            box.dataset.dictating = 'true';
            attach.click();
        }
        const locked = recording || !!attach || box.dataset.dictating === 'true';
        const send = box.querySelector('button[aria-label="Submit"]');
        if (send) {
            if (locked) {
                if (!send.dataset.dictationLocked) {
                    send.dataset.wasDisabled = String(send.disabled);
                    send.dataset.dictationLocked = 'true';
                }
                if (!send.disabled) send.disabled = true;
                send.title = 'Wait for dictation to become editable text';
            } else if (send.dataset.dictationLocked) {
                send.disabled = send.dataset.wasDisabled === 'true';
                delete send.dataset.dictationLocked;
                delete send.dataset.wasDisabled;
                send.title = 'Send';
            }
        }
        if (!box.dataset.guardInstalled) {
            box.dataset.guardInstalled = 'true';
            box.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' && !event.shiftKey &&
                    (box.dataset.dictating === 'true' || box.querySelector('button[aria-label="Stop recording"]'))) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                }
            }, true);
        }
    }
    new MutationObserver(sync).observe(root, {childList:true, subtree:true, attributes:true, attributeFilter:['data-dictating']});
    sync();
}
"""


with gr.Blocks() as demo:
    textbox = gr.MultimodalTextbox(
        sources=["microphone"], file_types=["audio"],
        placeholder="Type a message or use the microphone...",
        elem_id="message-box", submit_btn=True, stop_btn=True,
        show_label=False, container=False, render=False,
    )
    learning_state = gr.State(initial_state)
    chatbot = gr.Chatbot(value=[{"role": "assistant", "content": WELCOME}], label="Concept check")
    chat = gr.ChatInterface(
        fn=reply, textbox=textbox, chatbot=chatbot, multimodal=True, validator=validate_message,
        additional_inputs=[learning_state], additional_outputs=[learning_state],
        title="learning assistant Chatbot",
        description="PS SMS L1 practice: learn, explain your reasoning, get feedback, and retry.",
    )
    with gr.Accordion("Reference and L1 boundaries", open=False):
        gr.Markdown("Based on **PS SMS L1 User and Troubleshooting Reference v1.0, 20 October 2026**. "
                    "This supplied reference records observations and verification gaps, not a verified repair. "
                    "Keep checks read-only and escalate to the assigned SMS L2 owner. "
                    "Use fictional examples; do not enter patient data or secrets. "
                    "Answer feedback and voice transcription use Groq. Attempts stay in this session and are not saved to a training database.")
    applied_recording = gr.State(None)
    chatbot.clear(
        fn=lambda: (initial_state(), None),
        outputs=[learning_state, applied_recording], api_visibility="private",
    )
    dictation_event = textbox.change(
        fn=dictation_to_draft, inputs=[textbox, applied_recording],
        outputs=[textbox, applied_recording], api_name="dictate",
        concurrency_id="dictation", concurrency_limit=1,
        trigger_mode="always_last",
        js="""(message, state) => {
            const root = document.querySelector('gradio-app')?.shadowRoot || document;
            const box = root.querySelector('#message-box');
            if (box && message?.files?.length) box.dataset.dictating = 'true';
            return [message, state];
        }""",
    )
    dictation_event.then(fn=None, inputs=None, outputs=None, queue=False, api_visibility="private",
        js="""() => {
            const root = document.querySelector('gradio-app')?.shadowRoot || document;
            const box = root.querySelector('#message-box');
            if (box) box.dataset.dictating = 'false';
        }""")

demo.launch(
    inbrowser=True, share=False,
    css='button[aria-label="Retry"], button[aria-label="Undo"] { display: none !important; }',
    js=DICTATION_GUARD_JS,
)
