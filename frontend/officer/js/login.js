/** Minimal officer login for the page-level auth gate. */

const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const submitBtn = document.getElementById("submit-btn");
const officerInput = document.getElementById("officer-id");
const passwordInput = document.getElementById("password");

// In per-officer mode the gateway lists the valid operator IDs (names only,
// never secrets) so the field can offer a dropdown; in shared-password mode
// the list is empty and the field stays free-text.
async function populateOfficerList() {
  const datalist = document.getElementById("officer-list");
  if (!datalist) return;
  try {
    const response = await fetch("/api/auth/officers", { credentials: "same-origin" });
    if (!response.ok) return;
    const data = await response.json();
    for (const name of data.officers || []) {
      const option = document.createElement("option");
      option.value = name;
      datalist.appendChild(option);
    }
  } catch {
    // Dropdown is a convenience; free-text entry keeps working without it.
  }
}

populateOfficerList();

function showError(message) {
  if (errorBox) errorBox.textContent = message || "Authentication failed.";
}

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("");
    if (submitBtn) submitBtn.disabled = true;
    try {
      const officerId = officerInput.value.trim();
      const token = passwordInput.value;
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ officer_id: officerId, token }),
        credentials: "same-origin",
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        showError(data.detail || "Invalid operator or password.");
        return;
      }
      window.location.replace("/officer");
    } catch (err) {
      showError("Could not reach the dispatch gateway.");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}
