"""Where an exchange is written: the main window's transcript, the command box, or both.

The window and the command box run the same code (pipeline, permission gate, streaming); they only
differ in which of these views it writes to.
"""
TAGS = {
    "user": {"foreground": "#1f4e8c", "font": ("Helvetica", 12, "bold")},
    "bot": {"foreground": "#2d6a2d", "font": ("Helvetica", 12, "bold")},
    "note": {"foreground": "#888888", "font": ("Helvetica", 10, "italic")},
    "ok": {"foreground": "#2d6a2d"},
    "fail": {"foreground": "#b00020"},
}
LINK_STYLES = {"link": {"foreground": "#1f4e8c", "underline": True},
               "faint": {"foreground": "#aaaaaa", "font": ("Helvetica", 10)}}


class Link:
    """A clickable run of text; `add` appends text right after it (e.g. " — saved, thanks")."""

    def __init__(self, view, tag):
        self.view, self.tag = view, tag

    def add(self, text, tag=None):
        w = self.view.text
        w.configure(state="normal")
        w.insert(f"{self.tag}.last", text, tag)
        w.configure(state="disabled")


class Transcript:
    """One Tk Text widget: tagged text, clickable links and embedded widgets (approval cards)."""

    def __init__(self, text, on_change=None):
        self.text = text
        self.on_change = on_change or (lambda: None)
        self.links = 0
        for tag, opts in TAGS.items():
            text.tag_config(tag, **opts)

    @property
    def card_parent(self):
        return self.text

    def write(self, s, tag=None):
        self.text.configure(state="normal")
        self.text.insert("end", s, tag)
        self.text.configure(state="disabled")
        self.text.see("end")
        self.on_change()

    def link(self, s, on_click, style="link"):
        """Write `s` as a link; returns a list of Link handles (one per widget it went to)."""
        self.links += 1
        tag = f"{style}{self.links}"
        self.text.tag_config(tag, **LINK_STYLES[style])
        self.text.tag_bind(tag, "<Button-1>", lambda e: on_click())
        self.text.tag_bind(tag, "<Enter>", lambda e: self.text.config(cursor="pointinghand"))
        self.text.tag_bind(tag, "<Leave>", lambda e: self.text.config(cursor=""))
        self.write(s, tag)
        return [Link(self, tag)]

    def embed(self, widget, summary=""):
        self.text.configure(state="normal")
        self.text.window_create("end", window=widget)
        self.text.insert("end", "\n\n")
        self.text.configure(state="disabled")
        self.text.see("end")
        self.on_change()

    def card_status(self, s, tag=None):
        """How a card ended. The card shows it itself; plain views have nothing more to write."""

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.on_change()


class Tee:
    """The command box with the main window as a record: everything goes to both, except that the
    card itself lives in the box and the window gets one line about it, then how it ended."""

    def __init__(self, primary, mirror):
        self.primary, self.mirror = primary, mirror

    @property
    def card_parent(self):
        return self.primary.card_parent

    def write(self, s, tag=None):
        self.primary.write(s, tag)
        self.mirror.write(s, tag)

    def link(self, s, on_click, style="link"):
        return self.primary.link(s, on_click, style) + self.mirror.link(s, on_click, style)

    def embed(self, widget, summary=""):
        self.primary.embed(widget)
        if summary:
            self.mirror.write(summary + "\n", "note")

    def card_status(self, s, tag=None):
        self.mirror.write(s + "\n\n", tag)

    def clear(self):
        self.primary.clear()
