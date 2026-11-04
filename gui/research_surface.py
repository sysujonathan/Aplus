"""Keep dense research desks readable at native DPI; scroll instead of shrinking."""
import tkinter as tk
import ttkbootstrap as ttk

from .theme import APP_BG


def attach_research_surface(page, minimum=(1150, 650), sticky_rows=2):
    canvas = tk.Canvas(page, bg=APP_BG, highlightthickness=0)
    body = ttk.Frame(canvas)
    content = canvas.create_window(0, 0, anchor='nw', window=body)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    canvas.grid(row=sticky_rows, column=0, sticky='nsew')
    vertical = ttk.Scrollbar(page, command=canvas.yview)
    vertical.grid(row=sticky_rows, column=1, sticky='ns')
    horizontal = ttk.Scrollbar(page, orient='horizontal', command=canvas.xview)
    horizontal.grid(row=sticky_rows+1, column=0, sticky='ew')
    canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    page.columnconfigure(0, weight=1)
    page.columnconfigure(1, weight=0)
    page.rowconfigure(sticky_rows, weight=1)
    def fit(event):
        scale = page.winfo_fpixels('1i') / 96
        width, height = max(event.width, round(minimum[0]*scale)), max(event.height, round(minimum[1]*scale))
        canvas.itemconfigure(content, width=width, height=height)
        canvas.configure(scrollregion=(0, 0, width, height))
    canvas.bind('<Configure>', fit)
    page._research_canvas = canvas
    page._research_body = body
    page._research_minimum = minimum
