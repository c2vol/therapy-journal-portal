// Dictation using the browser's built-in speech recognition (Web Speech API).
(function () {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const button = document.getElementById("dictate");
  const note = document.getElementById("dictate-note");
  const box = document.getElementById("entry-text");
  if (Recognition && button) {
    button.hidden = false;
    note.hidden = false;
    let rec = null;
    button.addEventListener("click", () => {
      if (rec) { rec.stop(); return; }
      rec = new Recognition();
      rec.continuous = true;
      rec.interimResults = false;
      rec.lang = navigator.language || "en-US";
      rec.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i++) {
          if (event.results[i].isFinal) {
            const text = event.results[i][0].transcript.trim();
            box.value += (box.value && !box.value.endsWith(" ") ? " " : "") + text;
          }
        }
      };
      rec.onend = () => { rec = null; button.textContent = "🎤 Dictate"; };
      rec.onerror = () => { rec = null; button.textContent = "🎤 Dictate"; };
      rec.start();
      button.textContent = "⏹ Stop dictating";
    });
  }

  document.querySelectorAll(".delete-form").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!confirm("Delete this entry for good?")) e.preventDefault();
    });
  });

  const summary = document.getElementById("summary-form");
  if (summary) {
    summary.addEventListener("submit", () => {
      const b = summary.querySelector("button");
      b.disabled = true;
      b.textContent = "Summarizing… (this can take a little while)";
    });
  }
})();
