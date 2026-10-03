"""
BIZZARE — Single-file prototype application
===============================================

Purpose
-------
Generate a Windows-installable TTF whose normal characters A-Z visually render
using a deterministic, key-dependent set of glyphs.

This is a VISUAL OBFUSCATION layer, not encryption.

Requirements
------------
Python 3.10+
fontTools

Install the only dependency:
    python -m pip install fonttools

Run:
    python Bizzare.py

The application intentionally starts with an EMPTY PIN field.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ============================================================================
# Dependency check
# ============================================================================

try:
    from fontTools.ttLib import TTFont
except ImportError:
    root = tk.Tk()
    root.withdraw()
    answer = messagebox.askyesno(
        "Bizzare dependency missing",
        "Bizzare needs the 'fonttools' package.\n\n"
        "Install it automatically with pip?"
    )
    root.destroy()

    if answer:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "fonttools"]
            )
            from fontTools.ttLib import TTFont
        except Exception as exc:
            messagebox.showerror(
                "Installation failed",
                f"Could not install fonttools.\n\n{exc}\n\n"
                "Run:\npython -m pip install fonttools"
            )
            raise SystemExit(1)
    else:
        raise SystemExit(
            "Bizzare requires fonttools. "
            "Install with: python -m pip install fonttools"
        )


# ============================================================================
# Auto-detect system font
# ============================================================================

def find_system_font() -> Path | None:
    """
    Locate a usable TTF font on the system without user interaction.

    Searches the Windows Fonts directory first, then common cross-platform
    locations. Returns the first font found that contains the required
    visual glyphs.
    """
    candidates = [
        # Windows
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cour.ttf",
        "C:/Windows/Fonts/times.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/verdana.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        # macOS
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]

    for candidate in candidates:
        p = Path(candidate)
        if p.exists():
            try:
                font = TTFont(str(p))
                cmaps = [t for t in font["cmap"].tables if t.isUnicode()]
                if cmaps:
                    # Check that the font has at least the visual alphabet
                    has_all = True
                    for ch in VISUAL_ALPHABET:
                        found = False
                        for t in cmaps:
                            if ord(ch) in t.cmap:
                                found = True
                                break
                        if not found:
                            has_all = False
                            break
                    if has_all:
                        return p
            except Exception:
                continue

    # Fallback: scan the Windows Fonts directory
    fonts_dir = Path("C:/Windows/Fonts")
    if fonts_dir.exists():
        for ttf in fonts_dir.glob("*.ttf"):
            try:
                font = TTFont(str(ttf))
                cmaps = [t for t in font["cmap"].tables if t.isUnicode()]
                if cmaps:
                    return ttf
            except Exception:
                continue

    return None


# ============================================================================
# Cryptographic mapping engine
# ============================================================================

SOURCE_ALPHABET = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

# 26 very obvious ASCII visual glyphs.
# These are only glyph sources for the prototype.
VISUAL_ALPHABET = list(
    "!@#$%^&*()-_=+[]{};:,.?/<>~"
)[:26]


def new_salt() -> bytes:
    """Create a fresh 128-bit profile salt."""
    return secrets.token_bytes(16)


def derive_master_key(pin: str, salt: bytes) -> bytes:
    """
    Convert the user's PIN into a 256-bit key.

    The PIN itself is NOT used as the mapping key.
    """
    if not pin.isdigit():
        raise ValueError("PIN must contain digits only.")

    if len(pin) < 8:
        raise ValueError("PIN must contain at least 8 digits.")

    # Prototype parameters. These can be hardened after profiling on target
    # machines. The salt is public profile metadata, not a secret.
    return hashlib.scrypt(
        pin.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )


def prf_stream(key: bytes, domain: str, length: int) -> bytes:
    """Expand HMAC-SHA256 into deterministic pseudorandom bytes."""
    result = bytearray()
    counter = 0

    while len(result) < length:
        msg = (
            domain.encode("utf-8")
            + b"|"
            + counter.to_bytes(8, "big")
        )
        result.extend(hmac.new(key, msg, hashlib.sha256).digest())
        counter += 1

    return bytes(result[:length])


def uniform_index(stream: bytes, upper_bound: int, offset: int):
    """Unbiased deterministic integer in [0, upper_bound)."""
    limit = 2**32 - (2**32 % upper_bound)

    while offset + 4 <= len(stream):
        value = int.from_bytes(
            stream[offset:offset + 4],
            "big",
        )
        offset += 4

        if value < limit:
            return value % upper_bound, offset

    raise RuntimeError("Random stream exhausted.")


def generate_mapping(
    master_key: bytes,
    mapping_id: str,
) -> dict[str, str]:
    """
    Generate a deterministic one-to-one mapping.

    Same:
        master key + mapping ID
    gives the same mapping.

    Different:
        key or mapping ID
    gives a different mapping.
    """
    targets = list(VISUAL_ALPHABET)

    stream = prf_stream(
        master_key,
        f"Bizzare:mapping:{mapping_id}",
        4096,
    )

    offset = 0

    for i in range(len(targets) - 1, 0, -1):
        j, offset = uniform_index(
            stream,
            i + 1,
            offset,
        )
        targets[i], targets[j] = targets[j], targets[i]

    return dict(zip(SOURCE_ALPHABET, targets))


def reverse_mapping(mapping: dict[str, str]) -> dict[str, str]:
    """Create the inverse mapping: visual -> normal."""
    return {v: k for k, v in mapping.items()}


# ============================================================================
# Font engine
# ============================================================================

def find_unicode_cmaps(font: TTFont):
    """Return all Unicode cmap subtables."""
    tables = [
        table
        for table in font["cmap"].tables
        if table.isUnicode()
    ]

    if not tables:
        raise RuntimeError(
            "The selected font has no Unicode cmap table."
        )

    return tables


def get_glyph_for_character(
    cmap_tables,
    character: str,
) -> str:
    """
    Find the glyph name for a character.

    Search every Unicode cmap table because fonts can contain several
    subtables (Windows Unicode, Unicode BMP, format 4, format 12, etc.).
    """
    codepoint = ord(character)

    for table in cmap_tables:
        glyph_name = table.cmap.get(codepoint)
        if glyph_name:
            return glyph_name

    raise RuntimeError(
        f"The selected base font does not contain glyph {character!r}."
    )


def remap_font(
    base_font_path: Path,
    output_path: Path,
    mapping: dict[str, str],
    family_name: str,
):
    """
    Create a TTF where A-Z render using the glyphs belonging to the
    corresponding visual source characters.

    Example:
        mapping["A"] == "@"

    means:
        Unicode A -> glyph normally used for @
    """
    font = TTFont(str(base_font_path))

    unicode_cmaps = find_unicode_cmaps(font)

    # Resolve visual glyph names BEFORE changing any cmap.
    glyph_for_visual = {}

    for visual_character in VISUAL_ALPHABET:
        glyph_for_visual[visual_character] = get_glyph_for_character(
            unicode_cmaps,
            visual_character,
        )

    # Update every Unicode cmap table (both uppercase and lowercase).
    for normal_character, visual_character in mapping.items():
        upper_codepoint = ord(normal_character.upper())
        lower_codepoint = ord(normal_character.lower())
        visual_glyph_name = glyph_for_visual[visual_character]

        for table in unicode_cmaps:
            # Map both cases to the same visual glyph.
            table.cmap[upper_codepoint] = visual_glyph_name
            table.cmap[lower_codepoint] = visual_glyph_name

    # Set font family/name records.
    if "name" in font:
        name_table = font["name"]

        replacements = {
            1: family_name,
            2: "Regular",
            4: f"{family_name} Regular",
            6: family_name.replace(" ", "-"),
        }

        for record in name_table.names:
            if record.nameID in replacements:
                value = replacements[record.nameID]

                try:
                    record.string = value.encode(record.getEncoding())
                except Exception:
                    record.string = value.encode("utf-16-be")

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    font.save(str(output_path))

    # Reload the saved font immediately. If this fails, don't tell the user
    # that generation succeeded.
    verify_generated_font(
        output_path,
        mapping,
    )


def verify_generated_font(
    font_path: Path,
    mapping: dict[str, str],
):
    """
    Verify the actual saved TTF, not merely the in-memory font.

    This catches the class of bug where a font saves successfully but the
    desired Unicode mappings aren't actually present after reload.
    """
    font = TTFont(str(font_path))
    unicode_cmaps = find_unicode_cmaps(font)

    for normal_character, expected_visual in mapping.items():
        # Verify both uppercase and lowercase codepoints.
        for variant in (normal_character.upper(), normal_character.lower()):
            codepoint = ord(variant)

            found = False

            for table in unicode_cmaps:
                glyph_name = table.cmap.get(codepoint)

                if glyph_name is None:
                    continue

                # The visual character must resolve to the same glyph name in
                # the saved font.
                visual_glyph = get_glyph_for_character(
                    unicode_cmaps,
                    expected_visual,
                )

                if glyph_name == visual_glyph:
                    found = True
                    break

            if not found:
                raise RuntimeError(
                    f"Font verification failed for {variant}."
                )


# ============================================================================
# Color palette & theme constants
# ============================================================================

COLORS = {
    "bg_dark":        "#0f1117",
    "bg_panel":       "#181b24",
    "bg_input":       "#1e2230",
    "bg_card":        "#232838",
    "border":         "#2d3348",
    "border_accent":  "#3a7bfd",
    "text_primary":   "#e8eaf0",
    "text_secondary": "#8890a4",
    "text_dim":       "#5a6178",
    "accent":         "#3a7bfd",
    "accent_hover":   "#5090ff",
    "accent_muted":   "#1a3264",
    "salt_bg":        "#1a2a1e",
    "salt_border":    "#2d8a4e",
    "salt_text":      "#4ade80",
    "warning":        "#f59e0b",
    "error":          "#ef4444",
    "success":        "#22c55e",
    "tab_active":     "#3a7bfd",
    "tab_inactive":   "#2d3348",
}

FONT_HEADING    = ("Segoe UI", 22, "bold")
FONT_SUBHEADING = ("Segoe UI", 11)
FONT_LABEL      = ("Segoe UI", 10)
FONT_LABEL_BOLD = ("Segoe UI", 10, "bold")
FONT_INPUT      = ("Consolas", 11)
FONT_MONO       = ("Consolas", 12)
FONT_MONO_LG    = ("Consolas", 16)
FONT_SALT       = ("Consolas", 13, "bold")
FONT_BUTTON     = ("Segoe UI", 10, "bold")
FONT_STATUS     = ("Segoe UI", 9)
FONT_TAB        = ("Segoe UI", 10, "bold")
FONT_SECTION    = ("Segoe UI", 11, "bold")


# ============================================================================
# UI
# ============================================================================

class BizzareApp(tk.Tk):

    def __init__(self):
        super().__init__()

        self.title("Bizzare")
        self.geometry("1060x820")
        self.minsize(900, 720)
        self.configure(bg=COLORS["bg_dark"])

        self.salt = new_salt()
        self.mapping: dict[str, str] = {}
        self.system_font_path: Path | None = None

        self.pin_var = tk.StringVar()
        self.mapping_id_var = tk.StringVar(value="font-001")
        self.base_font_var = tk.StringVar()
        self.salt_var = tk.StringVar(value=self.salt.hex())
        self.preview_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value="  Enter a PIN and generate a mapping to begin."
        )

        # Decipher tab variables
        self.decipher_pin_var = tk.StringVar()
        self.decipher_mapping_id_var = tk.StringVar(value="font-001")
        self.decipher_salt_var = tk.StringVar()
        self.decipher_status_var = tk.StringVar(
            value="  Paste cipher text and enter the same PIN + salt + mapping ID to decipher."
        )

        self._detect_system_font()
        self._build_ui()

    def _detect_system_font(self):
        """Auto-detect a usable system font."""
        self.system_font_path = find_system_font()
        if self.system_font_path:
            self.base_font_var.set(str(self.system_font_path))

    # ------------------------------------------------------------------
    # Widget helpers
    # ------------------------------------------------------------------

    def _make_label(self, parent, text, font=FONT_LABEL, fg=None, **kw):
        """Create a themed label."""
        return tk.Label(
            parent,
            text=text,
            font=font,
            bg=kw.pop("bg", parent.cget("bg")),
            fg=fg or COLORS["text_primary"],
            **kw,
        )

    def _make_entry(self, parent, textvariable, width=30, show=None,
                    state="normal", font=FONT_INPUT, **kw):
        """Create a themed entry."""
        e = tk.Entry(
            parent,
            textvariable=textvariable,
            width=width,
            show=show,
            state=state,
            font=font,
            bg=COLORS["bg_input"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["accent"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            disabledbackground=COLORS["bg_panel"],
            disabledforeground=COLORS["text_secondary"],
            readonlybackground=COLORS["bg_panel"],
            **kw,
        )
        return e

    def _make_button(self, parent, text, command, accent=False, **kw):
        """Create a themed button."""
        bg = COLORS["accent"] if accent else COLORS["bg_card"]
        fg = "#ffffff" if accent else COLORS["text_primary"]
        abg = COLORS["accent_hover"] if accent else COLORS["border"]

        btn = tk.Button(
            parent,
            text=text,
            command=command,
            font=FONT_BUTTON,
            bg=bg,
            fg=fg,
            activebackground=abg,
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=6,
            bd=0,
            highlightthickness=0,
            **kw,
        )
        btn.bind("<Enter>", lambda e, b=btn, c=abg: b.config(bg=c))
        btn.bind("<Leave>", lambda e, b=btn, c=bg: b.config(bg=c))
        return btn

    def _make_section_header(self, parent, text):
        """Create a section header label."""
        return tk.Label(
            parent,
            text=text,
            font=FONT_SECTION,
            bg=parent.cget("bg"),
            fg=COLORS["accent"],
            anchor="w",
        )

    # ------------------------------------------------------------------
    # Build the UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Main container
        main = tk.Frame(self, bg=COLORS["bg_dark"])
        main.pack(fill="both", expand=True, padx=20, pady=15)

        # ── Header ──
        header = tk.Frame(main, bg=COLORS["bg_dark"])
        header.pack(fill="x", pady=(0, 12))

        tk.Label(
            header,
            text="◆  Bizzare",
            font=FONT_HEADING,
            bg=COLORS["bg_dark"],
            fg=COLORS["text_primary"],
        ).pack(side="left")

        tk.Label(
            header,
            text="v0.2",
            font=("Segoe UI", 9),
            bg=COLORS["bg_dark"],
            fg=COLORS["text_dim"],
        ).pack(side="left", padx=(8, 0), pady=(8, 0))

        # ── Tab bar ──
        tab_bar = tk.Frame(main, bg=COLORS["bg_dark"])
        tab_bar.pack(fill="x")

        self.tab_frames = {}
        self.tab_buttons = {}
        self.current_tab = tk.StringVar(value="generate")

        for tab_id, tab_label in [("generate", "⚙  Generate"), ("decipher", "🔓  Decipher")]:
            btn = tk.Button(
                tab_bar,
                text=tab_label,
                font=FONT_TAB,
                bg=COLORS["tab_active"] if tab_id == "generate" else COLORS["tab_inactive"],
                fg="#ffffff" if tab_id == "generate" else COLORS["text_secondary"],
                relief="flat",
                bd=0,
                padx=20,
                pady=8,
                cursor="hand2",
                highlightthickness=0,
                command=lambda t=tab_id: self._switch_tab(t),
            )
            btn.pack(side="left", padx=(0, 2))
            self.tab_buttons[tab_id] = btn

        # ── Tab content area ──
        content_area = tk.Frame(main, bg=COLORS["bg_panel"],
                                highlightthickness=1,
                                highlightbackground=COLORS["border"])
        content_area.pack(fill="both", expand=True)

        # Generate tab
        gen_frame = tk.Frame(content_area, bg=COLORS["bg_panel"])
        gen_frame.pack(fill="both", expand=True)
        self.tab_frames["generate"] = gen_frame
        self._build_generate_tab(gen_frame)

        # Decipher tab
        dec_frame = tk.Frame(content_area, bg=COLORS["bg_panel"])
        self.tab_frames["decipher"] = dec_frame
        self._build_decipher_tab(dec_frame)

        # ── Status bar ──
        self.status_label = tk.Label(
            main,
            textvariable=self.status_var,
            font=FONT_STATUS,
            bg=COLORS["bg_card"],
            fg=COLORS["text_secondary"],
            anchor="w",
            padx=10,
            pady=6,
        )
        self.status_label.pack(fill="x", pady=(8, 0))

        # Focus PIN
        self.after(150, self.pin_entry.focus_set)

    def _switch_tab(self, tab_id):
        self.current_tab.set(tab_id)

        for tid, frame in self.tab_frames.items():
            if tid == tab_id:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

        for tid, btn in self.tab_buttons.items():
            if tid == tab_id:
                btn.config(bg=COLORS["tab_active"], fg="#ffffff")
            else:
                btn.config(bg=COLORS["tab_inactive"], fg=COLORS["text_secondary"])

        # Update status bar binding
        if tab_id == "generate":
            self.status_label.config(textvariable=self.status_var)
        else:
            self.status_label.config(textvariable=self.decipher_status_var)

    # ------------------------------------------------------------------
    # Generate tab
    # ------------------------------------------------------------------

    def _build_generate_tab(self, parent):
        pad = tk.Frame(parent, bg=COLORS["bg_panel"])
        pad.pack(fill="both", expand=True, padx=18, pady=14)

        # ── Salt panel (prominent) ──
        salt_outer = tk.Frame(pad, bg=COLORS["salt_border"], bd=0)
        salt_outer.pack(fill="x", pady=(0, 14))

        salt_panel = tk.Frame(salt_outer, bg=COLORS["salt_bg"])
        salt_panel.pack(fill="x", padx=2, pady=2)

        salt_header = tk.Frame(salt_panel, bg=COLORS["salt_bg"])
        salt_header.pack(fill="x", padx=14, pady=(10, 4))

        tk.Label(
            salt_header,
            text="🔑  PROFILE SALT",
            font=FONT_LABEL_BOLD,
            bg=COLORS["salt_bg"],
            fg=COLORS["salt_text"],
        ).pack(side="left")

        tk.Label(
            salt_header,
            text="Share this with your recipient — it is NOT secret",
            font=("Segoe UI", 8),
            bg=COLORS["salt_bg"],
            fg=COLORS["text_dim"],
        ).pack(side="left", padx=(12, 0))

        salt_row = tk.Frame(salt_panel, bg=COLORS["salt_bg"])
        salt_row.pack(fill="x", padx=14, pady=(2, 10))

        self.salt_display = tk.Entry(
            salt_row,
            textvariable=self.salt_var,
            state="readonly",
            font=FONT_SALT,
            width=40,
            bg=COLORS["bg_dark"],
            fg=COLORS["salt_text"],
            readonlybackground=COLORS["bg_dark"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["salt_border"],
            highlightcolor=COLORS["salt_border"],
        )
        self.salt_display.pack(side="left", fill="x", expand=True, ipady=4)

        self._make_button(
            salt_row, "📋 Copy", self._copy_salt
        ).pack(side="left", padx=(8, 4))

        self._make_button(
            salt_row, "🔄 New Salt", self.new_salt
        ).pack(side="left", padx=(0, 0))

        # ── Profile fields ──
        profile = tk.Frame(pad, bg=COLORS["bg_card"],
                           highlightthickness=1,
                           highlightbackground=COLORS["border"])
        profile.pack(fill="x", pady=(0, 10))

        prof_inner = tk.Frame(profile, bg=COLORS["bg_card"])
        prof_inner.pack(fill="x", padx=14, pady=12)

        self._make_section_header(prof_inner, "Font Profile").pack(anchor="w", pady=(0, 8))

        # PIN row
        pin_row = tk.Frame(prof_inner, bg=COLORS["bg_card"])
        pin_row.pack(fill="x", pady=3)

        self._make_label(pin_row, "PIN", fg=COLORS["text_secondary"],
                         font=FONT_LABEL_BOLD, width=12, anchor="w").pack(side="left")
        self.pin_entry = self._make_entry(pin_row, self.pin_var, width=28, show="•")
        self.pin_entry.pack(side="left", ipady=3)
        self._make_label(pin_row, "8+ digits only",
                         fg=COLORS["text_dim"], font=("Segoe UI", 8)).pack(side="left", padx=10)

        # Mapping ID row
        mid_row = tk.Frame(prof_inner, bg=COLORS["bg_card"])
        mid_row.pack(fill="x", pady=3)

        self._make_label(mid_row, "Mapping ID", fg=COLORS["text_secondary"],
                         font=FONT_LABEL_BOLD, width=12, anchor="w").pack(side="left")
        self._make_entry(mid_row, self.mapping_id_var, width=28).pack(side="left", ipady=3)
        self._make_label(mid_row, "Unique identifier for this font",
                         fg=COLORS["text_dim"], font=("Segoe UI", 8)).pack(side="left", padx=10)

        # Base font row
        bf_row = tk.Frame(prof_inner, bg=COLORS["bg_card"])
        bf_row.pack(fill="x", pady=3)

        self._make_label(bf_row, "Base Font", fg=COLORS["text_secondary"],
                         font=FONT_LABEL_BOLD, width=12, anchor="w").pack(side="left")
        bf_entry = self._make_entry(bf_row, self.base_font_var, width=45)
        bf_entry.pack(side="left", ipady=3, fill="x", expand=True)

        self._make_button(bf_row, "Browse…", self.browse_font).pack(side="left", padx=(8, 0))

        if self.system_font_path:
            auto_label = tk.Label(
                prof_inner,
                text=f"  ✓ Auto-detected: {self.system_font_path.name}",
                font=("Segoe UI", 8),
                bg=COLORS["bg_card"],
                fg=COLORS["success"],
            )
            auto_label.pack(anchor="w", pady=(2, 0))

        # ── Action buttons ──
        actions = tk.Frame(pad, bg=COLORS["bg_panel"])
        actions.pack(fill="x", pady=(4, 10))

        self._make_button(
            actions, "⚡  Generate / Preview Mapping",
            self.generate_mapping, accent=True
        ).pack(side="left", padx=(0, 8))

        self._make_button(
            actions, "💾  Create TTF Font",
            self.create_font
        ).pack(side="left")

        # ── Mapping table ──
        table_frame = tk.Frame(pad, bg=COLORS["bg_card"],
                               highlightthickness=1,
                               highlightbackground=COLORS["border"])
        table_frame.pack(fill="both", expand=True, pady=(0, 10))

        table_header = tk.Frame(table_frame, bg=COLORS["bg_card"])
        table_header.pack(fill="x", padx=12, pady=(8, 4))
        self._make_section_header(table_header, "Character Mapping").pack(anchor="w")

        tree_container = tk.Frame(table_frame, bg=COLORS["bg_dark"])
        tree_container.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Secret.Treeview",
            background=COLORS["bg_dark"],
            foreground=COLORS["text_primary"],
            fieldbackground=COLORS["bg_dark"],
            borderwidth=0,
            font=FONT_MONO,
            rowheight=26,
        )
        style.configure(
            "Secret.Treeview.Heading",
            background=COLORS["bg_card"],
            foreground=COLORS["accent"],
            font=FONT_LABEL_BOLD,
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "Secret.Treeview",
            background=[("selected", COLORS["accent_muted"])],
            foreground=[("selected", COLORS["text_primary"])],
        )
        style.map(
            "Secret.Treeview.Heading",
            background=[("active", COLORS["bg_card"])],
        )

        columns = ("normal", "visual")
        self.tree = ttk.Treeview(
            tree_container,
            columns=columns,
            show="headings",
            height=10,
            style="Secret.Treeview",
        )
        self.tree.heading("normal", text="  Normal Character")
        self.tree.heading("visual", text="  Mapped Glyph")
        self.tree.column("normal", width=300, anchor="center")
        self.tree.column("visual", width=300, anchor="center")

        scroll = ttk.Scrollbar(
            tree_container,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # ── Visual preview ──
        preview_panel = tk.Frame(pad, bg=COLORS["bg_card"],
                                 highlightthickness=1,
                                 highlightbackground=COLORS["border"])
        preview_panel.pack(fill="x")

        prev_inner = tk.Frame(preview_panel, bg=COLORS["bg_card"])
        prev_inner.pack(fill="x", padx=14, pady=10)

        self._make_section_header(prev_inner, "Visual Preview").pack(anchor="w")

        tk.Label(
            prev_inner,
            text="A B C D E F G H I J K L M N O P Q R S T U V W X Y Z",
            font=("Consolas", 10),
            bg=COLORS["bg_card"],
            fg=COLORS["text_dim"],
        ).pack(anchor="w", pady=(6, 2))

        tk.Label(
            prev_inner,
            textvariable=self.preview_var,
            font=FONT_MONO_LG,
            bg=COLORS["bg_card"],
            fg=COLORS["warning"],
        ).pack(anchor="w", pady=(0, 2))

    # ------------------------------------------------------------------
    # Decipher tab
    # ------------------------------------------------------------------

    def _build_decipher_tab(self, parent):
        pad = tk.Frame(parent, bg=COLORS["bg_panel"])
        pad.pack(fill="both", expand=True, padx=18, pady=14)

        # ── Instructions ──
        tk.Label(
            pad,
            text="Paste the secret-encoded text and provide the same PIN, salt, and mapping ID\n"
                 "that were used to generate the font. The original plaintext will appear below.",
            font=("Segoe UI", 9),
            bg=COLORS["bg_panel"],
            fg=COLORS["text_secondary"],
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        # ── Salt input (prominent) ──
        salt_outer = tk.Frame(pad, bg=COLORS["salt_border"], bd=0)
        salt_outer.pack(fill="x", pady=(0, 12))

        salt_panel = tk.Frame(salt_outer, bg=COLORS["salt_bg"])
        salt_panel.pack(fill="x", padx=2, pady=2)

        salt_inner = tk.Frame(salt_panel, bg=COLORS["salt_bg"])
        salt_inner.pack(fill="x", padx=14, pady=10)

        tk.Label(
            salt_inner,
            text="🔑  PROFILE SALT",
            font=FONT_LABEL_BOLD,
            bg=COLORS["salt_bg"],
            fg=COLORS["salt_text"],
        ).pack(side="left")

        self.decipher_salt_entry = tk.Entry(
            salt_inner,
            textvariable=self.decipher_salt_var,
            font=FONT_SALT,
            width=40,
            bg=COLORS["bg_dark"],
            fg=COLORS["salt_text"],
            insertbackground=COLORS["salt_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["salt_border"],
            highlightcolor=COLORS["salt_border"],
        )
        self.decipher_salt_entry.pack(side="left", padx=(12, 0), fill="x", expand=True, ipady=4)

        # ── Profile fields ──
        profile = tk.Frame(pad, bg=COLORS["bg_card"],
                           highlightthickness=1,
                           highlightbackground=COLORS["border"])
        profile.pack(fill="x", pady=(0, 12))

        prof_inner = tk.Frame(profile, bg=COLORS["bg_card"])
        prof_inner.pack(fill="x", padx=14, pady=12)

        self._make_section_header(prof_inner, "Decryption Profile").pack(anchor="w", pady=(0, 8))

        # PIN
        pin_row = tk.Frame(prof_inner, bg=COLORS["bg_card"])
        pin_row.pack(fill="x", pady=3)
        self._make_label(pin_row, "PIN", fg=COLORS["text_secondary"],
                         font=FONT_LABEL_BOLD, width=12, anchor="w").pack(side="left")
        self._make_entry(pin_row, self.decipher_pin_var, width=28, show="•").pack(side="left", ipady=3)

        # Mapping ID
        mid_row = tk.Frame(prof_inner, bg=COLORS["bg_card"])
        mid_row.pack(fill="x", pady=3)
        self._make_label(mid_row, "Mapping ID", fg=COLORS["text_secondary"],
                         font=FONT_LABEL_BOLD, width=12, anchor="w").pack(side="left")
        self._make_entry(mid_row, self.decipher_mapping_id_var, width=28).pack(side="left", ipady=3)

        # ── Cipher text input ──
        cipher_frame = tk.Frame(pad, bg=COLORS["bg_card"],
                                highlightthickness=1,
                                highlightbackground=COLORS["border"])
        cipher_frame.pack(fill="both", expand=True, pady=(0, 10))

        cipher_inner = tk.Frame(cipher_frame, bg=COLORS["bg_card"])
        cipher_inner.pack(fill="both", expand=True, padx=14, pady=10)

        self._make_section_header(cipher_inner, "Cipher Text  →  Input").pack(anchor="w", pady=(0, 6))

        self.cipher_text = tk.Text(
            cipher_inner,
            height=5,
            font=FONT_MONO,
            bg=COLORS["bg_dark"],
            fg=COLORS["warning"],
            insertbackground=COLORS["accent"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            wrap="word",
        )
        self.cipher_text.pack(fill="both", expand=True)

        # ── Decipher button ──
        btn_row = tk.Frame(pad, bg=COLORS["bg_panel"])
        btn_row.pack(fill="x", pady=(0, 10))

        self._make_button(
            btn_row, "🔓  Decipher Text",
            self._do_decipher, accent=True
        ).pack(side="left")

        self._make_button(
            btn_row, "🗑  Clear",
            self._clear_decipher
        ).pack(side="left", padx=(8, 0))

        # ── Plaintext output ──
        plain_frame = tk.Frame(pad, bg=COLORS["bg_card"],
                               highlightthickness=1,
                               highlightbackground=COLORS["border"])
        plain_frame.pack(fill="both", expand=True)

        plain_inner = tk.Frame(plain_frame, bg=COLORS["bg_card"])
        plain_inner.pack(fill="both", expand=True, padx=14, pady=10)

        self._make_section_header(plain_inner, "Deciphered Plaintext  →  Output").pack(anchor="w", pady=(0, 6))

        self.plain_text = tk.Text(
            plain_inner,
            height=5,
            font=FONT_MONO,
            bg=COLORS["bg_dark"],
            fg=COLORS["success"],
            insertbackground=COLORS["accent"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            state="disabled",
            wrap="word",
        )
        self.plain_text.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # Generate-tab actions
    # ------------------------------------------------------------------

    def _copy_salt(self):
        self.clipboard_clear()
        self.clipboard_append(self.salt_var.get())
        self.status_var.set("  ✓ Salt copied to clipboard.")

    def new_salt(self):
        self.salt = new_salt()
        self.salt_var.set(self.salt.hex())

        self.mapping = {}
        self.clear_mapping_table()

        self.preview_var.set("")

        self.status_var.set(
            "  🔄 New salt generated. Generate a new mapping."
        )

    def browse_font(self):
        path = filedialog.askopenfilename(
            title="Choose a base font",
            filetypes=[
                (
                    "TrueType / OpenType fonts",
                    "*.ttf *.otf",
                ),
                (
                    "TrueType fonts",
                    "*.ttf",
                ),
                (
                    "OpenType fonts",
                    "*.otf",
                ),
                (
                    "All files",
                    "*.*",
                ),
            ],
        )

        if path:
            self.base_font_var.set(path)

    def validate_profile(self):
        pin = self.pin_var.get().strip()

        if not pin:
            messagebox.showwarning(
                "PIN required",
                "Enter your PIN before generating a mapping.",
            )
            self.pin_entry.focus_set()
            return None

        if not pin.isdigit():
            messagebox.showerror(
                "Invalid PIN",
                "The PIN must contain digits only.",
            )
            self.pin_entry.focus_set()
            return None

        if len(pin) < 8:
            messagebox.showerror(
                "PIN too short",
                "Use at least 8 digits.",
            )
            self.pin_entry.focus_set()
            return None

        mapping_id = self.mapping_id_var.get().strip()

        if not mapping_id:
            messagebox.showerror(
                "Mapping ID required",
                "Enter a mapping ID.",
            )
            return None

        return pin, mapping_id

    def generate_mapping(self):
        profile = self.validate_profile()

        if profile is None:
            return

        pin, mapping_id = profile

        try:
            key = derive_master_key(
                pin,
                self.salt,
            )

            self.mapping = generate_mapping(
                key,
                mapping_id,
            )

        except Exception as exc:
            messagebox.showerror(
                "Mapping generation failed",
                str(exc),
            )
            return

        self.populate_mapping_table()

        visual = "  ".join(
            self.mapping[ch]
            for ch in SOURCE_ALPHABET
        )

        self.preview_var.set(visual)

        self.status_var.set(
            "  ✓ Mapping generated. Same PIN + salt + Mapping ID reproduces it."
        )

    def populate_mapping_table(self):
        self.clear_mapping_table()

        for normal_character in SOURCE_ALPHABET:
            self.tree.insert(
                "",
                "end",
                values=(
                    normal_character,
                    self.mapping[normal_character],
                ),
            )

    def clear_mapping_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def create_font(self):
        profile = self.validate_profile()

        if profile is None:
            return

        pin, mapping_id = profile

        base_path = self.base_font_var.get().strip()

        if not base_path:
            messagebox.showwarning(
                "Base font required",
                "No system font was auto-detected and no font was selected.\n\n"
                "Please browse to a TTF/OTF font file.",
            )
            return

        base_font = Path(base_path)

        if not base_font.exists():
            messagebox.showerror(
                "Base font not found",
                "The selected base font does not exist.",
            )
            return

        # Generate mapping if user hasn't already done so.
        try:
            key = derive_master_key(
                pin,
                self.salt,
            )

            self.mapping = generate_mapping(
                key,
                mapping_id,
            )

            self.populate_mapping_table()

            self.preview_var.set(
                "  ".join(
                    self.mapping[ch]
                    for ch in SOURCE_ALPHABET
                )
            )

        except Exception as exc:
            messagebox.showerror(
                "Mapping generation failed",
                str(exc),
            )
            return

        safe_id = "".join(
            c if c.isalnum() or c in "-_"
            else "_"
            for c in mapping_id
        )

        output = filedialog.asksaveasfilename(
            title="Save Bizzare TTF",
            defaultextension=".ttf",
            initialfile=f"Bizzare-{safe_id}.ttf",
            filetypes=[
                (
                    "TrueType font",
                    "*.ttf",
                ),
            ],
        )

        if not output:
            return

        try:
            family_name = f"Bizzare {mapping_id}"

            remap_font(
                base_font,
                Path(output),
                self.mapping,
                family_name,
            )

        except Exception as exc:
            messagebox.showerror(
                "Font generation failed",
                str(exc),
            )

            self.status_var.set(
                "  ✗ Font generation failed."
            )

            return

        self.status_var.set(
            f"  ✓ Created and verified: {Path(output).name}"
        )

        messagebox.showinfo(
            "Bizzare created",
            "The TTF was created and internally verified.\n\n"
            f"File:\n{output}\n\n"
            "Install it in Windows, select it in Notepad or another "
            "text editor, and type A-Z to test the visual mapping.",
        )

    # ------------------------------------------------------------------
    # Decipher-tab actions
    # ------------------------------------------------------------------

    def _do_decipher(self):
        """Reverse-map cipher text back to plaintext."""
        pin = self.decipher_pin_var.get().strip()

        if not pin:
            messagebox.showwarning("PIN required", "Enter the PIN that was used to generate the font.")
            return

        if not pin.isdigit() or len(pin) < 8:
            messagebox.showerror("Invalid PIN", "The PIN must be 8+ digits.")
            return

        salt_hex = self.decipher_salt_var.get().strip()
        if not salt_hex:
            messagebox.showwarning("Salt required", "Enter the profile salt (hex string) from the generator.")
            return

        try:
            salt = bytes.fromhex(salt_hex)
        except ValueError:
            messagebox.showerror("Invalid salt", "The salt must be a valid hexadecimal string.")
            return

        mapping_id = self.decipher_mapping_id_var.get().strip()
        if not mapping_id:
            messagebox.showerror("Mapping ID required", "Enter the mapping ID.")
            return

        try:
            key = derive_master_key(pin, salt)
            fwd = generate_mapping(key, mapping_id)
            rev = reverse_mapping(fwd)
        except Exception as exc:
            messagebox.showerror("Decipher failed", str(exc))
            return

        cipher = self.cipher_text.get("1.0", "end").strip()
        if not cipher:
            messagebox.showwarning("No text", "Paste the cipher text to decipher.")
            return

        plaintext = []
        for ch in cipher:
            if ch in rev:
                plaintext.append(rev[ch])
            elif ch.upper() in rev:
                plaintext.append(rev[ch.upper()])
            else:
                # Non-mapped characters pass through unchanged.
                plaintext.append(ch)

        result = "".join(plaintext)

        self.plain_text.config(state="normal")
        self.plain_text.delete("1.0", "end")
        self.plain_text.insert("1.0", result)
        self.plain_text.config(state="disabled")

        self.decipher_status_var.set(
            f"  ✓ Deciphered {len(cipher)} characters successfully."
        )

    def _clear_decipher(self):
        """Clear both text areas in the decipher tab."""
        self.cipher_text.delete("1.0", "end")
        self.plain_text.config(state="normal")
        self.plain_text.delete("1.0", "end")
        self.plain_text.config(state="disabled")
        self.decipher_status_var.set(
            "  Cleared. Paste new cipher text to decipher."
        )


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    app = BizzareApp()
    app.mainloop()
