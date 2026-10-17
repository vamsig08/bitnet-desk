"""Menu-bar icon (Tk 9's built-in `tk systray`, an NSStatusItem on macOS) and its menu."""

# 18×18 icon: a rounded square with three dots, for BitNet's ternary −1 / 0 / +1 weights
ICON = [
    "..################..",
    ".#................#.",
    "#..................#",
    "#..................#",
    "#..................#",
    "#..................#",
    "#..................#",
    "#...##...##...##...#",
    "#..###..###..###...#",
    "#..###..###..###...#",
    "#...##...##...##...#",
    "#..................#",
    "#..................#",
    "#..................#",
    "#..................#",
    "#..................#",
    ".#................#.",
    "..################..",
]


def icon_image(root):
    import tkinter as tk
    img = tk.PhotoImage(master=root, width=len(ICON[0]), height=len(ICON))
    for y, row in enumerate(ICON):
        for x, ch in enumerate(row):
            if ch == "#":
                img.put("#000000", to=(x, y))
            else:
                img.transparency_set(x, y, True)
    return img


class MenuBar:
    """`items`: list of (label, callback) | None (separator) | ("check", label, variable, callback)
    | ("note", label). `.ok` is False when the Tk build has no systray (or show_icon is False)."""

    def __init__(self, root, tooltip, items, show_icon=True):
        import tkinter as tk
        self.root = root
        self.menu = tk.Menu(root, tearoff=0)
        for item in items:
            if item is None:
                self.menu.add_separator()
            elif item[0] == "check":
                _, label, var, cb = item
                self.menu.add_checkbutton(label=label, variable=var, command=cb)
            elif item[0] == "note":
                self.menu.add_command(label=item[1], state="disabled")
            else:
                self.menu.add_command(label=item[0], command=item[1])
        self.image = icon_image(root)
        popup = root.register(self.popup)
        self.ok = False
        if not show_icon:
            return
        try:
            root.tk.call("tk", "systray", "create", "-image", self.image, "-text", tooltip,
                         "-button1", popup, "-button3", popup)
            self.ok = True
        except tk.TclError:
            self.ok = False

    def popup(self, *args):
        x, y = self.root.winfo_pointerxy()
        self.menu.tk_popup(x, y)

    def destroy(self):
        if self.ok:
            try:
                self.root.tk.call("tk", "systray", "destroy")
            except Exception:
                pass
            self.ok = False
