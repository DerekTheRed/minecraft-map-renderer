import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
from pathlib import Path

from PIL import Image as PILImage, ImageTk

from renderer import render

FONT = ("JetBrains Mono", 11)
FONT_SMALL = ("JetBrains Mono", 9)
FONT_BOLD = ("JetBrains Mono", 11, "bold")
FONT_SMALL_BOLD = ("JetBrains Mono", 9, "bold")

PADX = 12
PADY = 5

def main():
    progressQueue = queue.Queue()


    def openWorldFolder():
        folder = filedialog.askdirectory()
        if folder:
            worldDirectory.delete(0, tk.END)
            worldDirectory.insert(0, folder)


    def chooseOutputFile():
        filename = filedialog.asksaveasfilename(
            title="Save Render As",
            defaultextension=".png",
            filetypes=[("PNG Images", "*.png")]
        )
        if filename:
            outputFile.delete(0, tk.END)
            outputFile.insert(0, filename)


    def updateWorldSize(event=None):
        try:
            dx = int(seXEntry.get()) - int(nwXEntry.get())
            dz = int(seZEntry.get()) - int(nwZEntry.get())
            canvas.itemconfig(worldSizeLabel, text=f"{dx:,} x {dz:,}, {dx * dz:,} Blocks")
        except ValueError:
            canvas.itemconfig(worldSizeLabel, text="Enter valid coordinates")


    def setFormEnabled(enabled):
        state = "normal" if enabled else "disabled"
        for widget in (worldDirectory, outputFile, nwXEntry, nwZEntry, seXEntry, seZEntry, renderButton):
            widget.config(state=state)


    def renderMap():
        world = worldDirectory.get().strip()
        output = outputFile.get().strip()

        if not world:
            messagebox.showerror("Missing world", "Please choose a Minecraft world folder.")
            return
        if not output:
            messagebox.showerror("Missing output", "Please choose an output PNG file.")
            return

        try:
            # NW is inclusive, SE is exclusive - passed straight through to render()
            topLeft = (int(nwXEntry.get()), int(nwZEntry.get()))
            bottomRight = (int(seXEntry.get()), int(seZEntry.get()))
        except ValueError:
            messagebox.showerror("Invalid coordinates", "Please enter valid integer coordinates.")
            return

        setFormEnabled(False)
        progressBar.config(value=0, maximum=100)
        canvas.itemconfig(statusLabel, text="Starting render...")

        def onProgress(current, total):
            progressQueue.put(("progress", current, total))

        def worker():
            try:
                unknownCount = render(world, topLeft, bottomRight, output, progress_callback=onProgress)
                progressQueue.put(("done", unknownCount))
            except Exception as e:
                progressQueue.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        root.after(100, pollProgress)


    def pollProgress():
        try:
            while True:
                item = progressQueue.get_nowait()
                kind = item[0]

                if kind == "progress":
                    _, current, total = item
                    progressBar.config(maximum=total, value=current)
                    canvas.itemconfig(statusLabel, text=f"Rendering row {current}/{total}")

                elif kind == "done":
                    _, unknownCount = item
                    progressBar.config(value=progressBar["maximum"])
                    msg = "Render complete!"
                    if unknownCount:
                        msg += f" ({unknownCount} unknown block type(s))"
                    canvas.itemconfig(statusLabel, text=msg)
                    setFormEnabled(True)
                    return

                elif kind == "error":
                    _, err = item
                    canvas.itemconfig(statusLabel, text="Render failed.")
                    setFormEnabled(True)
                    messagebox.showerror("Render failed", err)
                    return
        except queue.Empty:
            pass
        root.after(100, pollProgress)


    # Window

    WINDOW_WIDTH, WINDOW_HEIGHT = 600, 360

    root = tk.Tk()
    root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
    root.title("Minecraft Map Renderer")
    root.resizable(False, False)

    try:
        root.iconphoto(True, tk.PhotoImage(file="assets/icon.png"))
    except Exception:
        pass

    # Canvas (background image + everything drawn on top of it)
    #
    # Labels/frames always paint an opaque rectangle in Tk, which is why text used
    # to blot out the background image. Drawing text straight onto a Canvas has no
    # background rectangle at all, so it reads as "transparent" over the image.
    # Entry/Button widgets are embedded with canvas.create_window(...) so they
    # keep their normal opaque appearance.

    canvas = tk.Canvas(root, width=WINDOW_WIDTH, height=WINDOW_HEIGHT, highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)

    # Flat white/black widget styling

    ENTRY_STYLE = dict(
        bg="white", fg="black", insertbackground="black",
        relief="flat", bd=0, highlightthickness=0,
    )
    BUTTON_STYLE = dict(
        bg="white", fg="black", activebackground="white", activeforeground="black",
        relief="flat", bd=0, highlightthickness=0,
    )

    style = ttk.Style()
    style.theme_use("clam")  # clam actually honors style overrides; native themes bake in a fixed border

    # Redefine the layout with no border element at all (some themes draw a
    # beveled border regardless of borderwidth/bordercolor settings)
    style.layout(
        "Flat.Horizontal.TProgressbar",
        [
            (
                "Horizontal.Progressbar.trough",
                {
                    "sticky": "nswe",
                    "children": [
                        ("Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})
                    ],
                },
            )
        ],
    )
    style.configure(
        "Flat.Horizontal.TProgressbar",
        troughcolor="black", background="white",
        troughrelief="flat", relief="flat",
        borderwidth=0, thickness=16,
    )

    try:
        bgPath = Path(__file__).resolve().parent / "assets/bg.png"
        bgSource = PILImage.open(bgPath)
        scale = WINDOW_HEIGHT / bgSource.height
        bgSize = (int(bgSource.width * scale), WINDOW_HEIGHT)
        bgResized = bgSource.resize(bgSize, PILImage.LANCZOS).convert("RGBA")

        # 50% black tint, composited in PIL (Tkinter canvas images have no alpha blending of their own)
        tintOverlay = PILImage.new("RGBA", bgSize, (0, 0, 0, 128))
        bgTinted = PILImage.alpha_composite(bgResized, tintOverlay)
        bgPhoto = ImageTk.PhotoImage(bgTinted)

        bgOffsetX = (WINDOW_WIDTH - bgSize[0]) // 2
        canvas.create_image(bgOffsetX, 0, anchor="nw", image=bgPhoto)
        canvas.image = bgPhoto  # keep a reference so it isn't garbage collected
    except Exception as e:
        print("Could not load background image:", e)

    TEXT_FILL = "white"

    # Shared flat, borderless, white-on-black styling for every Entry/Button
    ENTRY_STYLE = dict(bg="white", fg="black", relief="flat", bd=0,
                        highlightthickness=0, insertbackground="black")
    BUTTON_STYLE = dict(bg="white", fg="black", relief="flat", bd=0,
                        highlightthickness=0, activebackground="white", activeforeground="black")

    # World Folder

    canvas.create_text(PADX, 16, anchor="nw", text="Minecraft World Folder:", font=FONT_BOLD, fill=TEXT_FILL)

    ENTRY_RIGHT_MARGIN = PADX
    BROWSE_WIDTH_PX = 80  # explicit pixel width forced on the button window so it can't overflow the canvas
    GAP = 8
    ENTRY_WIDTH_PX = WINDOW_WIDTH - PADX - ENTRY_RIGHT_MARGIN - BROWSE_WIDTH_PX - GAP

    worldDirectory = tk.Entry(root, font=FONT, **ENTRY_STYLE)
    canvas.create_window(PADX, 44, anchor="nw", window=worldDirectory, width=ENTRY_WIDTH_PX, height=26)

    worldBrowseX = PADX + ENTRY_WIDTH_PX + GAP
    worldBrowseBtn = tk.Button(root, text="Browse", font=FONT, command=openWorldFolder, **BUTTON_STYLE)
    canvas.create_window(worldBrowseX, 44, anchor="nw", window=worldBrowseBtn, width=BROWSE_WIDTH_PX, height=26)

    # Output File

    canvas.create_text(PADX, 84, anchor="nw", text="Output PNG File:", font=FONT_BOLD, fill=TEXT_FILL)

    outputFile = tk.Entry(root, font=FONT, **ENTRY_STYLE)
    canvas.create_window(PADX, 112, anchor="nw", window=outputFile, width=ENTRY_WIDTH_PX, height=26)

    outputBrowseBtn = tk.Button(root, text="Browse", font=FONT, command=chooseOutputFile, **BUTTON_STYLE)
    canvas.create_window(worldBrowseX, 112, anchor="nw", window=outputBrowseBtn, width=BROWSE_WIDTH_PX, height=26)

    # Render Area


    CORNER_ENTRY_WIDTH_PX = 60
    CORNER_LABEL_GAP = 22   # space reserved for "X:"/"Z:" label before its entry
    CORNER_FIELD_GAP = 10   # space between the X field and the "Z:" label
    CORNER_ROW_WIDTH = CORNER_LABEL_GAP + CORNER_ENTRY_WIDTH_PX + CORNER_FIELD_GAP + CORNER_LABEL_GAP + CORNER_ENTRY_WIDTH_PX


    def addCorner(xStart, name, alignRight=False):
        titleAnchor = "ne" if alignRight else "nw"
        titleX = xStart + CORNER_ROW_WIDTH if alignRight else xStart
        canvas.create_text(titleX, 156, anchor=titleAnchor, text=name, font=FONT_BOLD, fill=TEXT_FILL)

        rowY = 182
        canvas.create_text(xStart, rowY + 4, anchor="nw", text="X:", font=FONT_BOLD, fill=TEXT_FILL)
        xEntry = tk.Entry(root, width=8, font=FONT, **ENTRY_STYLE)
        canvas.create_window(xStart + CORNER_LABEL_GAP, rowY, anchor="nw", window=xEntry,
                            width=CORNER_ENTRY_WIDTH_PX, height=26)

        zLabelX = xStart + CORNER_LABEL_GAP + CORNER_ENTRY_WIDTH_PX + CORNER_FIELD_GAP
        canvas.create_text(zLabelX, rowY + 4, anchor="nw", text="Z:", font=FONT_BOLD, fill=TEXT_FILL)
        zEntry = tk.Entry(root, width=8, font=FONT, **ENTRY_STYLE)
        canvas.create_window(zLabelX + CORNER_LABEL_GAP, rowY, anchor="nw", window=zEntry,
                            width=CORNER_ENTRY_WIDTH_PX, height=26)

        return xEntry, zEntry


    nwXEntry, nwZEntry = addCorner(PADX, "Northwest (incl.)")
    seXStart = WINDOW_WIDTH - PADX - CORNER_ROW_WIDTH
    seXEntry, seZEntry = addCorner(seXStart, "Southeast (excl.)", alignRight=True)

    # World Size Display

    worldSizeLabel = canvas.create_text(
        PADX, 222, anchor="nw", text="Enter valid coordinates", font=FONT_BOLD, fill=TEXT_FILL
    )

    for entry in [nwXEntry, nwZEntry, seXEntry, seZEntry]:
        entry.bind("<KeyRelease>", updateWorldSize)

    # Render Button

    renderButton = tk.Button(root, text="Render Map", font=FONT, width=15, command=renderMap, **BUTTON_STYLE)
    canvas.create_window(WINDOW_WIDTH // 2, 260, anchor="n", window=renderButton)

    # Progress + Status

    progressBar = ttk.Progressbar(root, mode="determinate", style="Flat.Horizontal.TProgressbar")
    canvas.create_window(PADX, 315, anchor="nw", window=progressBar, width=WINDOW_WIDTH - 2 * PADX)

    statusLabel = canvas.create_text(PADX, 338, anchor="nw", text="", font=FONT_SMALL_BOLD, fill="white")

    root.mainloop()

if __name__ == "__main__":
    main()