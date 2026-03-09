/**
 * Voice (TTS + Speech Recognition) and Camera (WebRTC) functionality.
 */

import {
  state,
  messageInput,
  voiceSelect,
  cameraModal,
  cameraPreview,
  snapBtn,
  captureCanvas,
  imagePreview,
  previewImg,
  micBtn,
} from "./state.js";

// ── Voice Output (TTS) ─────────────────────────────────────────
export function populateVoices() {
  function loadVoices() {
    const voices = speechSynthesis.getVoices();
    if (!voiceSelect) return;
    voiceSelect.innerHTML = "";
    voices.forEach((v, i) => {
      const opt = document.createElement("option");
      opt.value = i;
      opt.textContent = `${v.name} (${v.lang})`;
      if (v.default) opt.selected = true;
      voiceSelect.appendChild(opt);
    });
  }
  loadVoices();
  speechSynthesis.onvoiceschanged = loadVoices;
}

export function speak(text) {
  if (!state.voiceEnabled || !text) return;
  const u = new SpeechSynthesisUtterance(text.replace(/[#*`_~]/g, ""));
  const voices = speechSynthesis.getVoices();
  if (voiceSelect && voices[voiceSelect.value]) u.voice = voices[voiceSelect.value];
  speechSynthesis.speak(u);
}

// ── Voice Input (Speech Recognition) ────────────────────────────
let recognition = null;
let isListening = false;

export function initSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    console.log("Speech recognition not supported");
    return;
  }

  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = "en-US";

  let finalTranscript = "";

  recognition.onstart = () => {
    isListening = true;
    if (micBtn) {
      micBtn.classList.add("mic-active");
      micBtn.title = "Stop listening";
    }
    console.log("[Voice] Listening started");
  };

  recognition.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        finalTranscript += transcript + " ";
      } else {
        interim += transcript;
      }
    }
    if (messageInput) {
      messageInput.value = finalTranscript + interim;
      messageInput.dispatchEvent(new Event("input", { bubbles: true }));
    }
  };

  recognition.onerror = (event) => {
    console.warn("[Voice] Error:", event.error);
    if (event.error === "not-allowed") {
      alert("Microphone permission denied. Please allow microphone access.");
    }
    isListening = false;
    if (micBtn) {
      micBtn.classList.remove("mic-active");
      micBtn.title = "Voice input";
    }
  };

  recognition.onend = () => {
    if (isListening) {
      try {
        recognition.start();
      } catch {
        isListening = false;
      }
    }
    if (micBtn) {
      micBtn.classList.remove("mic-active");
      micBtn.title = "Voice input";
    }
  };
}

export function startListening() {
  if (!recognition) {
    initSpeechRecognition();
    if (!recognition) return;
  }
  try {
    recognition.start();
  } catch (e) {
    console.warn("[Voice] Start failed:", e);
  }
}

export function stopListening() {
  isListening = false;
  if (recognition) {
    try {
      recognition.stop();
    } catch {
      /* ignore */
    }
  }
  if (micBtn) {
    micBtn.classList.remove("mic-active");
    micBtn.title = "Voice input";
  }
}

export function toggleMic() {
  if (isListening) {
    stopListening();
  } else {
    startListening();
  }
}

// ── Camera (WebRTC) ─────────────────────────────────────────────
let cameraStream = null;

export async function openCamera() {
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
    if (cameraPreview) cameraPreview.srcObject = cameraStream;
    if (cameraModal) cameraModal.style.display = "";
  } catch (e) {
    console.error("Camera error:", e);
    alert("Could not access camera.");
  }
}

export function closeCamera() {
  if (cameraStream) {
    cameraStream.getTracks().forEach((t) => t.stop());
    cameraStream = null;
  }
  if (cameraPreview) cameraPreview.srcObject = null;
  if (cameraModal) cameraModal.style.display = "none";
}

export function captureFrame() {
  if (!cameraPreview || !captureCanvas) return;
  const ctx = captureCanvas.getContext("2d");
  captureCanvas.width = cameraPreview.videoWidth;
  captureCanvas.height = cameraPreview.videoHeight;
  ctx.drawImage(cameraPreview, 0, 0);
  state.capturedImage = captureCanvas.toDataURL("image/jpeg", 0.8);
  if (imagePreview) imagePreview.style.display = "";
  if (previewImg) previewImg.src = state.capturedImage;
  closeCamera();
}

export function clearCapturedImage() {
  state.capturedImage = null;
  if (imagePreview) imagePreview.style.display = "none";
  if (previewImg) previewImg.src = "";
}
