// Read aloud (speech synthesis) and speaking a question (speech recognition), using the browser's built-in voices.

export const speechSupported = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;

const baseLang = (lang) => lang.toLowerCase().replace("_", "-").split("-")[0];

function voicesFor(lang) {
  const wanted = lang.toLowerCase();
  const voices = speechSynthesis.getVoices();
  const exact = voices.filter((v) => v.lang.toLowerCase().replace("_", "-") === wanted);
  return exact.length ? exact : voices.filter((v) => baseLang(v.lang) === baseLang(lang));
}

export function hasVoiceFor(lang) {
  return speechSupported && voicesFor(lang).length > 0;
}

// Reads a list of parts ({el, speech, lang}) one at a time, highlighting each part's element as it goes.
// Short utterances avoid browsers cutting off long text.
export function createReader({ getRate, onChange }) {
  let queue = [];
  let current = null;
  let paused = false;

  function clearHighlight() {
    if (current) current.el.classList.remove("speaking");
    current = null;
  }

  function finish() {
    clearHighlight();
    paused = false;
    onChange({ reading: false, paused: false });
  }

  function speakNext() {
    clearHighlight();
    const part = queue.shift();
    if (!part) {
      finish();
      return;
    }
    current = part;
    part.el.classList.add("speaking");
    const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    part.el.scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" });

    try {
      const utterance = new SpeechSynthesisUtterance(part.speech);
      utterance.lang = part.lang;
      const [voice] = voicesFor(part.lang);
      if (voice) utterance.voice = voice;
      utterance.rate = getRate();
      utterance.onend = () => { if (current === part) speakNext(); };
      utterance.onerror = (event) => {
        if (event.error === "interrupted" || event.error === "canceled") return;
        if (current === part) speakNext();
      };
      speechSynthesis.speak(utterance);
    } catch {
      // If the browser's speech engine fails, stop cleanly instead of leaving the controls stuck.
      queue = [];
      finish();
    }
  }

  return {
    read(parts) {
      if (!speechSupported) return;
      this.stop();
      queue = parts.filter((p) => p.speech);
      onChange({ reading: true, paused: false });
      speakNext();
    },
    stop() {
      if (!speechSupported) return;
      queue = [];
      clearHighlight();
      speechSynthesis.cancel();
      finish();
    },
    togglePause() {
      if (paused) speechSynthesis.resume();
      else speechSynthesis.pause();
      paused = !paused;
      onChange({ reading: true, paused });
    },
  };
}

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
export const dictationSupported = Boolean(Recognition);

// Turns speech into text in a textarea. Words are added after anything already typed.
export function createDictation({ textarea, getLang, onStart, onStop, onError }) {
  let recognition = null;

  function start() {
    recognition = new Recognition();
    recognition.lang = getLang();
    recognition.interimResults = true;
    recognition.continuous = true;
    const before = textarea.value.trim() ? `${textarea.value.trim()} ` : "";
    recognition.onresult = (event) => {
      let heard = "";
      for (let i = 0; i < event.results.length; i++) heard += event.results[i][0].transcript;
      textarea.value = before + heard.trim();
    };
    recognition.onerror = (event) => onError(event.error);
    recognition.onend = () => {
      recognition = null;
      onStop();
    };
    recognition.start();
    onStart();
  }

  return {
    toggle() {
      if (recognition) recognition.stop();
      else start();
    },
    stop() {
      if (recognition) recognition.stop();
    },
  };
}
