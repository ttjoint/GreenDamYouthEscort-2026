/**
 * Green Dam Youth Escort 2026 — service worker / background script.
 *
 * One job: relay an image to the local scoring service. The content script
 * cannot call `127.0.0.1` directly under Manifest V3 because the page's own
 * Content-Security-Policy would apply to the request; a message to the worker
 * does not inherit it.
 *
 * Why the scoring is a local service rather than a model bundled in the
 * extension: an ONNX runtime plus a 21 MB model inside a browser extension is
 * not something any store would accept, and the same model is already needed by
 * the host for other paths. One model, one place.
 *
 * Privacy: the bytes go to `127.0.0.1` and nowhere else. Nothing is stored. If
 * the service is not running, the image is not scored - and an unscored image is
 * left visible rather than hidden, because "not looked at" is not a finding.
 */
"use strict";

/** The local scoring endpoint. Matches `Rules.service_url` on the Python side. */
const DEFAULT_SERVICE = "http://127.0.0.1:17871";

/** How long to wait for the service before giving up. */
const TIMEOUT_MS = 5000;

/**
 * POST image bytes to the local classifier and return `{score}` or null.
 *
 * The service is expected to answer `{"score": 0.0-1.0, "assessed": true}`.
 * `assessed: false` means the service declined to judge the frame - a flat
 * colour, an unsupported format - and is returned as null rather than as zero.
 */
async function scoreWithService(buffer, contentType, serviceUrl) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await fetch(`${serviceUrl}/score`, {
      method: "POST",
      headers: { "Content-Type": contentType || "application/octet-stream" },
      body: buffer,
      signal: controller.signal,
    });
    if (!response.ok) return null;

    const payload = await response.json();
    if (!payload || payload.assessed === false) return null;
    if (typeof payload.score !== "number") return null;
    return payload;
  } catch (error) {
    console.debug("[greendam] scoring service unavailable", error);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "score-image") return false;

  (async () => {
    const config = await chrome.storage.local.get({ serviceUrl: DEFAULT_SERVICE });
    const result = await scoreWithService(
      message.blob,
      message.contentType,
      config.serviceUrl || DEFAULT_SERVICE,
    );
    sendResponse(result);
  })();

  // Returning true keeps the message channel open for the async reply.
  return true;
});

/** On install, record that filtering starts enabled. */
chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.local.get({ enabled: true });
  await chrome.storage.local.set({ enabled: current.enabled });
});
