from __future__ import annotations

from pathlib import Path
import json
import wave

from weclaw.config import ASR_MODELS_DIR, ASR_PROVIDER, DEFAULT_VOSK_MODEL_NAME, VOSK_MODEL_DIR


class SpeechRecognitionError(Exception):
    """统一表示语音转文字失败。"""


class BaseSpeechProvider:
    """ASR Provider 抽象基类，后续可以替换成 Moonshine、FunASR 或 whisper.cpp。"""

    name = "base"

    def transcribe(self, audio_path: Path) -> str:
        raise NotImplementedError


class VoskSpeechProvider(BaseSpeechProvider):
    """Vosk 离线 ASR Provider。"""

    name = "vosk"

    def __init__(self, model_dir: str | None) -> None:
        if not model_dir:
            raise SpeechRecognitionError("VOSK_MODEL_DIR is not configured.")
        self.model_dir = model_dir
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from vosk import Model
            except ImportError as exc:
                raise SpeechRecognitionError("Vosk is not installed. Please install the 'vosk' package first.") from exc
            self._model = Model(self.model_dir)
        return self._model

    def transcribe(self, audio_path: Path) -> str:
        wav_path = audio_path.with_suffix(".wav")
        _convert_to_wav(audio_path, wav_path)

        try:
            from vosk import KaldiRecognizer
        except ImportError as exc:
            raise SpeechRecognitionError("Vosk is not installed. Please install the 'vosk' package first.") from exc

        with wave.open(str(wav_path), "rb") as wav_file:
            if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
                raise SpeechRecognitionError("Audio must be mono 16-bit PCM after conversion.")

            recognizer = KaldiRecognizer(self._load_model(), wav_file.getframerate())
            while True:
                data = wav_file.readframes(4000)
                if not data:
                    break
                recognizer.AcceptWaveform(data)

            result = json.loads(recognizer.FinalResult())
            text = result.get("text", "").strip()
            if not text:
                raise SpeechRecognitionError("ASR returned empty text.")
            return text


class DisabledSpeechProvider(BaseSpeechProvider):
    """未启用 ASR 时使用的 Provider，给出清晰提示。"""

    name = "none"

    def transcribe(self, audio_path: Path) -> str:
        raise SpeechRecognitionError(
            "ASR is not enabled. Set ASR_PROVIDER=vosk and put "
            f"{DEFAULT_VOSK_MODEL_NAME} under {ASR_MODELS_DIR}."
        )


def _convert_to_wav(input_path: Path, output_path: Path) -> None:
    """用 ffmpeg 把 Telegram 的 ogg/opus 转为 Vosk 更容易处理的 wav。"""

    import subprocess

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SpeechRecognitionError(f"ffmpeg conversion failed: {result.stderr.strip()}")


def build_speech_provider() -> BaseSpeechProvider:
    provider = ASR_PROVIDER.strip().lower()
    if provider == "vosk":
        return VoskSpeechProvider(VOSK_MODEL_DIR)
    return DisabledSpeechProvider()


def transcribe_voice(audio_path: Path) -> str:
    """统一语音转文字入口，Telegram handler 不直接依赖具体 ASR 实现。"""

    provider = build_speech_provider()
    return provider.transcribe(audio_path)
