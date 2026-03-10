const API_BASE = "http://localhost:7700";

// Context menu: right-click → "Save to Neuron"
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "save-page",
    title: "Save page to Neuron",
    contexts: ["page"],
  });
  chrome.contextMenus.create({
    id: "save-selection",
    title: "Save selection to Neuron",
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: "save-link",
    title: "Save link to Neuron",
    contexts: ["link"],
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "save-page") {
    savePage(tab.url, tab.title);
  } else if (info.menuItemId === "save-selection") {
    saveText(info.selectionText, tab.title, tab.url);
  } else if (info.menuItemId === "save-link") {
    savePage(info.linkUrl, info.linkUrl);
  }
});

// Message handler from popup
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "SAVE_PAGE") {
    savePage(msg.url, msg.title).then(sendResponse);
    return true; // async
  }
  if (msg.type === "SAVE_TEXT") {
    saveText(msg.text, msg.title, msg.url).then(sendResponse);
    return true;
  }
  if (msg.type === "SAVE_YOUTUBE") {
    saveYouTube(msg.url).then(sendResponse);
    return true;
  }
  if (msg.type === "GET_STATUS") {
    getStatus().then(sendResponse);
    return true;
  }
  if (msg.type === "ASK") {
    ask(msg.question).then(sendResponse);
    return true;
  }
  if (msg.type === "GET_SETTINGS") {
    getSettings().then(sendResponse);
    return true;
  }
  if (msg.type === "SAVE_SETTINGS") {
    saveSettings(msg.settings).then(sendResponse);
    return true;
  }
});

// Use chrome.storage.sync so settings persist across devices
async function getSettings() {
  const data = await chrome.storage.sync.get(["apiBase"]);
  return { apiBase: data.apiBase || API_BASE };
}

async function saveSettings(settings) {
  await chrome.storage.sync.set(settings);
  return { ok: true };
}

async function savePage(url, title) {
  const { apiBase } = await getSettings();
  try {
    const res = await fetch(`${apiBase}/ingest/url`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) return { ok: false, error: data.detail || `Server error (${res.status})` };
    return { ok: true, title: data.title, chunks: data.chunks };
  } catch (e) {
    return { ok: false, error: "Cannot reach Neuron server. Is it running on " + apiBase + "?" };
  }
}

async function saveText(text, title, url) {
  const { apiBase } = await getSettings();
  try {
    const res = await fetch(`${apiBase}/ingest/text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, title, source: "web" }),
    });
    const data = await res.json();
    if (!res.ok) return { ok: false, error: data.detail || `Server error (${res.status})` };
    return {
      ok: true,
      title: title || data.title || "Selection",
      chunks: data.chunks ?? data.chunks_created ?? 0,
    };
  } catch (e) {
    return { ok: false, error: "Cannot reach Neuron server. Is it running on " + apiBase + "?" };
  }
}

async function saveYouTube(url) {
  const { apiBase } = await getSettings();
  try {
    const res = await fetch(`${apiBase}/ingest/youtube`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) return { ok: false, error: data.detail || `Server error (${res.status})` };
    return { ok: true, title: data.title, chunks: data.chunks ?? data.chunks_created ?? 0 };
  } catch (e) {
    return { ok: false, error: "Cannot reach Neuron server. Is it running on " + apiBase + "?" };
  }
}

async function getStatus() {
  const { apiBase } = await getSettings();
  try {
    const res = await fetch(`${apiBase}/status`);
    if (!res.ok) return { error: `Server error (${res.status})` };
    return await res.json();
  } catch (e) {
    return { error: "Cannot reach Neuron server. Is it running?" };
  }
}

async function ask(question) {
  const { apiBase } = await getSettings();
  try {
    const res = await fetch(`${apiBase}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: question }),
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      return { error: errData.detail || `Server error (${res.status})` };
    }
    const data = await res.json();
    // Normalize: /ask returns { answer, sources, question }
    return { answer: data.answer || data.text || "No answer returned.", sources: data.sources || [] };
  } catch (e) {
    return { error: "Cannot reach Neuron server. Make sure it is running at " + apiBase };
  }
}
