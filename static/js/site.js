document.addEventListener("DOMContentLoaded", () => {
  const root = document.documentElement;
  const toggle = document.getElementById("themeToggle");
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  const themeStorageKey = "jobpilot-theme-v3";

  const setTheme = theme => {
    const safeTheme = theme === "dark" || theme === "light" ? theme : "light";
    root.dataset.theme = safeTheme;
    const dark = safeTheme === "dark";

    if (toggle) {
      toggle.setAttribute("aria-label", `Switch to ${dark ? "light" : "dark"} theme`);
      toggle.setAttribute("title", `Switch to ${dark ? "light" : "dark"} theme`);
      toggle.setAttribute("aria-pressed", String(dark));
    }

    if (themeMeta) themeMeta.content = dark ? "#120d0a" : "#fff9f4";

    try {
      localStorage.setItem(themeStorageKey, safeTheme);
    } catch {
      // The selected theme still works for this page if browser storage is unavailable.
    }
  };

  let preferredTheme = "light";
  try {
    const storedTheme = localStorage.getItem(themeStorageKey);
    if (storedTheme === "light" || storedTheme === "dark") {
      preferredTheme = storedTheme;
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
      preferredTheme = "light";
    }
  } catch {
    preferredTheme = "light";
  }

  setTheme(preferredTheme);

  if (toggle) {
    toggle.addEventListener("click", () => {
      setTheme(root.dataset.theme === "dark" ? "light" : "dark");
    });
  }

  const notificationsLink = document.querySelector("[data-notifications-link]");
  if (notificationsLink) {
    const countBadge = notificationsLink.querySelector("[data-notification-count]");
    const inbox = document.getElementById("notification-list");
    const announcements = document.getElementById("live-notification-announcements");
    let cursor = Number(notificationsLink.dataset.cursor || 0);

    const updateNotifications = async () => {
      if (document.hidden) return;
      const url = new URL(notificationsLink.dataset.updatesUrl, window.location.origin);
      url.searchParams.set("after_id", String(cursor));
      try {
        const response = await fetch(url, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`Notification refresh failed (${response.status}).`);
        const data = await response.json();
        if (countBadge) {
          countBadge.textContent = String(data.unread_count);
          countBadge.classList.toggle("d-none", data.unread_count === 0);
          notificationsLink.setAttribute(
            "aria-label",
            `Notifications${data.unread_count ? `, ${data.unread_count} unread` : ""}`
          );
        }

        data.notifications.forEach(notification => {
          cursor = Math.max(cursor, notification.id);
          const article = document.createElement("article");
          article.className = "list-row align-items-start";
          const content = document.createElement("div");
          content.className = "flex-grow-1";
          const title = document.createElement("b");
          title.textContent = notification.title;
          const message = document.createElement("p");
          message.className = "mb-1";
          message.textContent = notification.message;
          const date = document.createElement("small");
          date.textContent = new Date(notification.created_at).toLocaleString();
          content.append(title, message, date);
          article.append(content);
          if (notification.url) {
            const link = document.createElement("a");
            link.className = "btn btn-sm btn-outline-primary";
            link.href = new URL(notification.url, window.location.origin).href;
            link.textContent = "View application updates";
            article.append(link);
          }
          if (inbox) {
            inbox.prepend(article);
          } else if (announcements) {
            const announcement = document.createElement("div");
            announcement.className = "alert alert-info mt-3";
            announcement.setAttribute("role", "status");
            announcement.textContent = `${notification.title}: ${notification.message}`;
            announcements.prepend(announcement);
          }
        });
      } catch (error) {
        console.error("Could not refresh JobPilot notifications.", error);
      }
    };

    window.setInterval(updateNotifications, 30000);
    document.addEventListener("visibilitychange", updateNotifications);
  }

  const recommendationResults = document.querySelector("[data-recommendation-results]");
  if (recommendationResults) {
    let attempts = 0;
    let requestInFlight = false;
    const maxAttempts = 300;
    const pollRecommendations = async () => {
      const current = document.querySelector("[data-recommendation-results]");
      if (!current || requestInFlight) return;
      if (attempts >= maxAttempts) {
        const status = current.querySelector("[data-recommendation-status]");
        if (status) {
          status.classList.remove("panel");
          status.classList.add("alert", "alert-info");
          status.textContent = "Recommendations are taking longer than expected. Refresh the dashboard later to check again.";
        }
        return;
      }
      requestInFlight = true;
      attempts += 1;
      try {
        const response = await fetch(current.dataset.updatesUrl, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`Recommendation refresh failed (${response.status}).`);
        const data = await response.json();
        const parsed = new DOMParser().parseFromString(data.html, "text/html");
        const replacement = parsed.querySelector("[data-recommendation-results]");
        if (!replacement) throw new Error("Recommendation response did not contain results.");
        current.replaceWith(replacement);
        if (data.status === "queued" || data.status === "processing") {
          window.setTimeout(pollRecommendations, 3000);
        }
      } catch (error) {
        console.error("Could not refresh JobPilot recommendations.", error);
        if (attempts < maxAttempts) window.setTimeout(pollRecommendations, 10000);
      } finally {
        requestInFlight = false;
      }
    };

    if (recommendationResults.dataset.status === "queued" ||
        recommendationResults.dataset.status === "processing") {
      window.setTimeout(pollRecommendations, 1500);
    }
  }

  document.querySelectorAll("[data-fallback-src]").forEach(image => {
    image.addEventListener("error", () => {
      const fallback = image.dataset.fallbackSrc;
      if (fallback && image.src !== new URL(fallback, window.location.origin).href) {
        image.src = fallback;
      } else {
        image.hidden = true;
      }
    }, { once: false });
  });

  document.querySelectorAll("[data-profile-avatar]").forEach(image => {
    image.addEventListener("error", () => {
      image.hidden = true;
      const fallback = image.nextElementSibling;
      if (fallback) fallback.hidden = false;
    }, { once: true });
  });

  document.querySelectorAll("[data-share-job], [data-copy-job-link]").forEach(button => {
    button.addEventListener("click", async () => {
      const feedback = document.querySelector("[data-share-feedback]");
      try {
        if (button.hasAttribute("data-share-job") && navigator.share) {
          await navigator.share({ title: document.title, url: window.location.href });
          if (feedback) feedback.textContent = "Job link shared.";
        } else {
          await navigator.clipboard.writeText(window.location.href);
          if (feedback) feedback.textContent = "Job link copied.";
        }
      } catch (error) {
        if (error.name === "AbortError") return;
        if (feedback) feedback.textContent = "Could not share the link. Copy it from your browser address bar.";
        console.error("Could not share the JobPilot job link.", error);
      }
    });
  });

  document.querySelectorAll("[data-skill-editor]").forEach(editor => {
    const textarea = editor.querySelector("textarea");
    const entry = editor.querySelector("[data-skill-entry]");
    const tags = editor.querySelector("[data-skill-tags]");
    const addButton = editor.querySelector("[data-add-skill]");
    if (!textarea || !entry || !tags || !addButton) return;

    const currentSkills = () => [...new Set(
      textarea.value.split(/[,\n]/).map(skill => skill.trim()).filter(Boolean)
        .map(skill => skill.replace(/\s+/g, " "))
        .filter((skill, index, skills) => skills.findIndex(
          value => value.toLocaleLowerCase() === skill.toLocaleLowerCase()
        ) === index)
    )];
    const renderSkills = () => {
      const skills = currentSkills();
      textarea.value = skills.join(", ");
      tags.replaceChildren();
      skills.forEach(skill => {
        const tag = document.createElement("span");
        tag.className = "skill-tag-editor";
        const label = document.createElement("span");
        label.textContent = skill;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", `Remove ${skill}`);
        remove.addEventListener("click", () => {
          textarea.value = currentSkills()
            .filter(value => value.toLocaleLowerCase() !== skill.toLocaleLowerCase())
            .join(", ");
          textarea.dispatchEvent(new Event("input", { bubbles: true }));
          renderSkills();
        });
        tag.append(label, remove);
        tags.append(tag);
      });
    };
    const addSkill = () => {
      const value = entry.value.trim().replace(/\s+/g, " ");
      if (!value) return;
      const skills = currentSkills();
      if (!skills.some(skill => skill.toLocaleLowerCase() === value.toLocaleLowerCase())) {
        skills.push(value);
        textarea.value = skills.join(", ");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
      }
      entry.value = "";
      renderSkills();
    };
    addButton.addEventListener("click", addSkill);
    entry.addEventListener("keydown", event => {
      if (event.key === "Enter") {
        event.preventDefault();
        addSkill();
      }
    });
    textarea.addEventListener("input", renderSkills);
    renderSkills();
  });

  document.querySelectorAll("[data-profile-image-input]").forEach(input => {
    input.addEventListener("change", () => {
      const preview = document.querySelector("[data-profile-image-preview]");
      const file = input.files && input.files[0];
      if (!preview || !file) return;
      if (preview.dataset.objectUrl) URL.revokeObjectURL(preview.dataset.objectUrl);
      preview.src = URL.createObjectURL(file);
      preview.dataset.objectUrl = preview.src;
      preview.classList.remove("d-none");
    });
  });

  document.querySelectorAll("[data-resume-builder]").forEach(form => {
    const preview = document.querySelector("[data-resume-live-preview]");
    const templateInput = form.querySelector("#id_template");
    const refreshPreview = () => {
      form.querySelectorAll("[id^='id_']").forEach(input => {
        const key = input.id.slice(3);
        const target = preview && preview.querySelector(`[data-resume-preview="${key}"]`);
        if (!target) return;
        target.textContent = input.value || "";
        const section = target.closest(".resume-live-section");
        if (section) section.hidden = !input.value.trim();
        else target.hidden = !input.value.trim();
      });
      if (preview && templateInput) preview.dataset.template = templateInput.value;
    };
    form.addEventListener("input", refreshPreview);
    form.addEventListener("change", refreshPreview);
    refreshPreview();
  });

  document.querySelectorAll("[data-pdf-frame]").forEach(frame => {
    const baseUrl = frame.src.split("#")[0];
    const pageInput = document.querySelector("[data-pdf-page]");
    let zoom = 100;
    const updatePdfFragment = () => {
      const page = Math.max(1, Number.parseInt(pageInput && pageInput.value, 10) || 1);
      if (pageInput) pageInput.value = String(page);
      frame.src = `${baseUrl}#page=${page}&zoom=${zoom}`;
    };
    document.querySelectorAll("[data-pdf-zoom]").forEach(button => {
      button.addEventListener("click", () => {
        zoom = Math.max(50, Math.min(200, zoom + (button.dataset.pdfZoom === "in" ? 10 : -10)));
        updatePdfFragment();
      });
    });
    if (pageInput) pageInput.addEventListener("change", updatePdfFragment);
  });

  document.querySelectorAll("form[data-submit-once]").forEach(form => {
    form.addEventListener("submit", event => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "true";
      form.setAttribute("aria-busy", "true");
      const button = form.querySelector('button[type="submit"]');
      if (button) {
        button.disabled = true;
        button.textContent = button.dataset.submitLabel || "Saving…";
      }
    });
  });

  document.querySelectorAll('input[type="file"]').forEach(input => {
    input.classList.add("form-control");
    if (!input.hasAttribute("accept")) input.setAttribute("accept", ".pdf,.docx,.txt");
  });

  document.querySelectorAll('input:not([type]), input[type="text"], input[type="email"], input[type="password"], input[type="number"], textarea, select').forEach(el => {
    if (!el.classList.contains("form-control") && !el.classList.contains("form-select")) {
      el.classList.add(el.tagName === "SELECT" ? "form-select" : "form-control");
    }
  });
});
