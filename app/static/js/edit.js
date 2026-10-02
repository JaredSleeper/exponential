(() => {
  "use strict";

  const elements = Array.from(document.querySelectorAll("[data-copy-key]"));
  const original = new Map();
  const dirty = new Set();
  let activeElement = null;
  let resetButton = null;
  let saveButton = null;
  let countLabel = null;
  let errorLabel = null;

  function textOf(element) {
    return element.innerText.trim();
  }

  function showError(message) {
    errorLabel.textContent = message;
  }

  function updateCount() {
    const count = dirty.size;
    countLabel.textContent = `Editing page copy · ${count} unsaved`;
    saveButton.disabled = count === 0;
    window.onbeforeunload = count
      ? (event) => {
          event.preventDefault();
          event.returnValue = "";
        }
      : null;
  }

  function markChanged(element) {
    const key = element.dataset.copyKey;
    if (textOf(element) === original.get(key)) {
      dirty.delete(key);
    } else {
      dirty.add(key);
    }
    updateCount();
  }

  function csrfToken() {
    const cookie = document.cookie.split("; ").find((part) => part.startsWith("exp_csrf="));
    return cookie ? decodeURIComponent(cookie.slice("exp_csrf=".length)) : "";
  }

  async function postForm(url, fields) {
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
        body: new URLSearchParams({ csrf: csrfToken(), ...fields }),
      });
    } catch (_error) {
      showError("Couldn't save right now.");
      return false;
    }
    if (!response.ok) {
      showError(
        response.status === 403 || response.status === 404
          ? "Admin access required."
          : "Couldn't save right now.",
      );
      return false;
    }
    showError("");
    return true;
  }

  function placeResetButton() {
    if (!resetButton || !activeElement) return;
    const rect = activeElement.getBoundingClientRect();
    resetButton.style.top = `${Math.max(4, rect.top - 30)}px`;
    resetButton.style.left = `${Math.max(4, Math.min(window.innerWidth - 100, rect.right - 96))}px`;
  }

  function showResetButton(element) {
    activeElement = element;
    if (!resetButton) {
      resetButton = document.createElement("button");
      resetButton.type = "button";
      resetButton.className = "edit-reset";
      resetButton.textContent = "↺ default";
      resetButton.addEventListener("mousedown", (event) => event.preventDefault());
      resetButton.addEventListener("click", async () => {
        const elementToReset = activeElement;
        const key = elementToReset?.dataset.copyKey;
        if (!key) return;
        if (!(await postForm("/admin/copy/reset", { key }))) return;
        elementToReset.textContent = elementToReset.dataset.copyDefault;
        original.set(key, textOf(elementToReset));
        dirty.delete(key);
        updateCount();
        elementToReset.focus();
        placeResetButton();
      });
      document.body.appendChild(resetButton);
    }
    resetButton.hidden = false;
    placeResetButton();
  }

  elements.forEach((element) => {
    const key = element.dataset.copyKey;
    original.set(key, textOf(element));
    element.contentEditable = "plaintext-only";
    if (element.contentEditable !== "plaintext-only") {
      element.contentEditable = "true";
    }
    element.addEventListener("input", () => markChanged(element));
    element.addEventListener("focus", () => showResetButton(element));
    element.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (
          resetButton &&
          activeElement === element &&
          document.activeElement !== resetButton &&
          document.activeElement !== element
        ) {
          resetButton.hidden = true;
        }
      }, 0);
    });
  });

  const bar = document.createElement("div");
  bar.className = "edit-bar";
  countLabel = document.createElement("span");
  countLabel.dataset.editCount = "";
  errorLabel = document.createElement("span");
  errorLabel.className = "edit-error";
  errorLabel.dataset.editError = "";
  errorLabel.setAttribute("role", "alert");
  const actions = document.createElement("div");
  actions.className = "edit-actions";

  saveButton = document.createElement("button");
  saveButton.type = "button";
  saveButton.dataset.editSave = "";
  saveButton.textContent = "Save";
  saveButton.disabled = true;
  const discardButton = document.createElement("button");
  discardButton.type = "button";
  discardButton.dataset.editDiscard = "";
  discardButton.textContent = "Discard";
  const doneButton = document.createElement("button");
  doneButton.type = "button";
  doneButton.dataset.editDone = "";
  doneButton.textContent = "Done";
  actions.append(saveButton, discardButton, doneButton);
  bar.append(countLabel, errorLabel, actions);
  document.body.appendChild(bar);
  document.body.classList.add("copy-edit-mode");
  updateCount();

  saveButton.addEventListener("click", async () => {
    const changes = {};
    elements.forEach((element) => {
      const key = element.dataset.copyKey;
      if (dirty.has(key)) changes[key] = textOf(element);
    });
    if (!Object.keys(changes).length) return;
    saveButton.disabled = true;
    if (!(await postForm("/admin/copy", { changes: JSON.stringify(changes) }))) {
      updateCount();
      return;
    }
    Object.entries(changes).forEach(([key, value]) => original.set(key, value));
    elements.forEach((element) => markChanged(element));
  });

  discardButton.addEventListener("click", () => {
    window.onbeforeunload = null;
    window.location.reload();
  });

  doneButton.addEventListener("click", () => {
    const url = new URL(window.location.href);
    url.searchParams.delete("edit");
    window.location.href = `${url.pathname}${url.search}${url.hash}`;
  });

  document.addEventListener(
    "click",
    (event) => {
      if (!(event.target instanceof Element)) return;
      if (event.target.closest(".edit-bar, .edit-reset")) return;
      if (event.target.closest("a, button")) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true,
  );
  document.addEventListener(
    "submit",
    (event) => {
      if (event.target instanceof Element && event.target.closest(".edit-bar")) return;
      event.preventDefault();
      event.stopPropagation();
    },
    true,
  );
  window.addEventListener("scroll", placeResetButton, { passive: true });
  window.addEventListener("resize", placeResetButton);
})();
