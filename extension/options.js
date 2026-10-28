// extension/options.js - load and save the server address
const box = document.getElementById("server");
chrome.storage.sync.get({ server: "http://127.0.0.1:8000" }, (v) => { box.value = v.server; });
document.getElementById("save").onclick = () =>
  chrome.storage.sync.set({ server: box.value.trim() }, () => { document.getElementById("msg").textContent = "Saved"; });
