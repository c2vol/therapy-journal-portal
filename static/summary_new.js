document.getElementById("copy").addEventListener("click", async () => {
  const box = document.getElementById("prompt");
  const note = document.getElementById("copied");
  try {
    await navigator.clipboard.writeText(box.value);
  } catch (e) {
    box.select();
    document.execCommand("copy");
  }
  note.textContent = "Copied. Now paste it into a new Claude chat.";
});
