(function () {
  "use strict";

  const MARKER = "KUBEAI_TECHNICAL_DETAILS|";
  const DEFAULT_TITLE = "Détails techniques / Preuves";

  if (!document.getElementById("kubeai-ui-style")) {
    const style = document.createElement("style");
    style.id = "kubeai-ui-style";
    style.textContent =
      "details.kubeai-technical-details > summary{cursor:pointer;font-weight:600;padding:6px 0;}" +
      "details.kubeai-technical-details .cm-line:first-child{display:none!important;}";
    document.head.appendChild(style);
  }

  function enhance() {
    // Open WebUI renders fenced code with CodeMirror, not a <pre> element.
    document.querySelectorAll(".cm-editor").forEach(function (editor) {
      const content = editor.querySelector(".cm-content");
      const text = content ? (content.innerText || "") : "";
      if (!text.startsWith(MARKER)) return;
      const block = (function () {
        let node = editor;
        while (node && node !== document.body) {
          if (node.classList && node.classList.contains("relative")
              && node.classList.contains("flex") && node.classList.contains("flex-col")) {
            return node;
          }
          node = node.parentElement;
        }
        return editor.closest(".language-text") || editor.parentElement;
      })();
      if (!block || block.closest("details")) return;

      const newline = text.indexOf("\n");
      const first = newline >= 0 ? text.slice(0, newline) : text;
      const title = first.slice(MARKER.length).trim() || DEFAULT_TITLE;

      const details = document.createElement("details");
      details.className = "kubeai-technical-details";
      details.open = false;

      const summary = document.createElement("summary");
      summary.textContent = title + " ▾";
      details.appendChild(summary);

      block.parentNode.insertBefore(details, block);
      details.appendChild(block);

      // The marker line is metadata, not evidence.
      const firstLine = editor.querySelector(".cm-line");
      if (firstLine) firstLine.style.display = "none";
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enhance, { once: true });
  } else {
    enhance();
  }
  const observer = new MutationObserver(enhance);
  observer.observe(document.body, { childList: true, subtree: true });
})();
