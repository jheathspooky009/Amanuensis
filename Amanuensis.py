# ============================================
# AMANUENSIS -- A HUMAN-LIKE TYPING ASSISTANT
# ============================================
#
# Reads text from a user-selected file and types it into whatever
# window currently has focus, using randomized delays, common-bigram
# speed-ups, simulated typos and "reworking" mistakes, and long
# research-style breaks to emulate natural human typing.
#
# VERSION HISTORY (most recent first)
# ------------------------------------------------------------
# v5.7.0 (2026-10-13) -- Auto-pick a profile by window (Update #30)
#   - Added: Auto Profile by Window. Add rules that pair some words from a window
#     title (like "Google Docs") with a saved profile. When a run starts, the first
#     rule whose words appear in the target window's title loads its profile, so
#     each program gets its own typing style.
#   - Added: An "Auto Profile by Window" card on the Window tab with two new
#     settings (enable_auto_profile, off by default, and auto_profile_rules, an
#     empty list by default), restored on launch.
#   - Modified: The target window is the locked window when Lock typing is on,
#     otherwise the window that was active when Start was pressed. Clicking the
#     Start button makes Amanuensis itself active, so use the Start hotkey.
#   - Notes: Rules hold window words, so they are never saved into profiles and
#     are ignored when a profile is loaded. A rule can only name a file inside the
#     profiles folder. An auto-picked profile never changes hotkeys, theme, or
#     update settings. The log records only the profile name, never the window
#     title. Preview and the time estimate do not switch profiles. No new packages.
#
# v5.6.0 (2026-10-13) -- Flow and hesitation states (Update #31)
#   - Added: Typing moods. The typist drifts between three states: normal,
#     flow (a stretch of 80 to 270 characters typed about 15% faster), and
#     hesitant (a stretch of 40 to 150 characters typed about 25% slower).
#     Changes ease in over about 12 characters, so speed never jumps.
#   - Added: A "Flow & Hesitation" card on the Speed tab with two new settings
#     (enable_typing_moods, on by default, and typing_mood_strength, 100 by
#     default, 0 to 200), saved with profiles and restored on launch.
#   - Modified: The pre-start time estimate includes the small average change.
#     A session break or research break puts the typist back in the normal state.
#   - Notes: The mood changes the delay before fatigue drift is applied, so the
#     two stack. Only speed changes (no extra pauses, typos, or reworks). Resume
#     starts in the normal state. The log records only the mood and position.
#     No new packages.
#
# v5.5.0 (2026-10-13) -- Difficulty-aware thinking pauses (Update #33)
#   - Added: Thinking pauses that depend on how hard the text is. Before a hard
#     word (long, many syllables, numbers, an acronym, or rare letters) there is
#     a short stop, and before a very long or packed sentence (30+ words, 3+ hard
#     words, or 4+ commas, semicolons, and colons) there is a longer one.
#   - Added: A "Difficulty Pauses" card on the Speed tab with two new settings
#     (enable_difficulty_pauses, on by default, and difficulty_pause_strength,
#     100 by default, 0 to 300), saved with profiles and restored on launch.
#   - Modified: The pre-start time estimate includes the average time of these
#     pauses. Not every hard word pauses (about 7 in 10), there are at least 4
#     words between word pauses, and common long words ("important") never count.
#   - Notes: The spots are planned once before typing starts, only for text still
#     to be typed (so resume works). Protected text never gets a pause, and a pause
#     press or window switch during a wait is caught before the next character.
#     The log records only the spot and length, never the words. No new packages.
#
# ============================================

import pyautogui
import keyboard
import threading
import queue
import random
import re
import time
import json
import csv
import os
import sys
import hashlib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog
from tkinter import messagebox as native_messagebox

# CustomTkinter gives the whole app its rounded, modern look. It is required.
try:
    import customtkinter as ctk
except ImportError:
    _missing_root = tk.Tk()
    _missing_root.withdraw()
    native_messagebox.showerror(
        "Missing package",
        "Amanuensis needs the CustomTkinter package.\n\nInstall it with:\n    uv add customtkinter",
    )
    sys.exit(1)

try:
    import pygetwindow as gw
    HAS_PYGETWINDOW = True
except ImportError:
    HAS_PYGETWINDOW = False

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

# --------------------------------------------
# THREADING & STATE MANAGEMENT
# --------------------------------------------
# threading.Event objects (rather than plain booleans) keep the GUI
# thread and the background typing thread in sync without race
# conditions.
stop_event = threading.Event()
pause_event = threading.Event()
focus_lost_event = threading.Event()

# Protects every read-modify-write on resume_state.json. Both the
# background typing thread (autosave) and the main thread (Stop,
# start_typing, clear button) can write, so we must guard them.
_resume_store_lock = threading.Lock()

# --------------------------------------------
# CROSS-THREAD GUI DISPATCH
# --------------------------------------------
# Tkinter widgets are only safe to touch from the main thread. Two
# other threads need to trigger GUI actions: the `keyboard` library's
# own listener thread (for global hotkeys) and the background typing
# thread (on errors or when a run finishes). Instead of calling
# Tkinter functions directly from those threads, they drop a
# zero-argument callable into this queue; process_gui_queue() (run
# via window.after on the main thread) drains it and calls those
# functions safely, on the main thread, where they belong.
gui_action_queue = queue.Queue()


def run_on_main_thread(func):
    """Call from any background thread instead of touching Tkinter
    directly. Schedules func to run on the main thread shortly."""
    gui_action_queue.put(func)


def process_gui_queue():
    try:
        while True:
            func = gui_action_queue.get_nowait()
            func()
    except queue.Empty:
        pass
    window.after(50, process_gui_queue)


class TyperState:
    """Holds all mutable run-time state shared between the GUI thread
    and the background typing thread. Only simple attribute reads/
    writes happen across threads, which is safe under the GIL for
    these use cases."""

    def __init__(self):
        self.is_running = False
        self.chars_typed = 0
        self.total_chars = 0
        self.total_newlines = 0
        self.newlines_typed = 0
        self.start_time = 0
        self.paused_duration = 0.0  # accumulated pause + break time, excluded from WPM/ETA
        self.wpm = 0
        self.status = "Idle"
        self.next_mistake_words = 0
        self.next_break_paragraphs = 0
        self.logging_enabled = False
        self.log_entries = []  # list of (timestamp, event, detail)
        self.locked_window_title = ""
        self.settings = {}
        self.clean_text = ""  # set by type_text() after parse_input_markers(); used by stop_typing()
        self.left_mistakes = []  # mistakes purposely left in the text this run (Update #24)
        self.left_mistakes_resumed = False  # True if a resumed file may hide earlier-session mistakes
        self.blocked_seconds = 0.0  # total time paused by the person, auto-pause, or lost focus (Update #27)
        self.target_end_time = 0.0  # when the run should finish under a target time (0 = no target)
        self.session_breaks_left = 0  # long session breaks still ahead in the current file


state = TyperState()

# --------------------------------------------
# COMMON LETTER PAIRS (typed slightly faster)
# --------------------------------------------
COMMON_PAIRS = {'th', 'he', 'an', 'in', 'er', 're', 'nd', 'ou', 'en', 'to', 'nt', 'ha', 'it', 'is', 'or', 'as'}

# --------------------------------------------
# FINGER AND HAND TIMING (Update #28)
# --------------------------------------------
# Which hand and finger types each letter on a QWERTY keyboard (standard
# touch typing). Each letter maps to (hand, finger). Index fingers cover two
# columns of keys, so they own more letters than the other fingers.
FINGER_FOR_LETTER = {
    'q': ('left', 'pinky'), 'a': ('left', 'pinky'), 'z': ('left', 'pinky'),
    'w': ('left', 'ring'), 's': ('left', 'ring'), 'x': ('left', 'ring'),
    'e': ('left', 'middle'), 'd': ('left', 'middle'), 'c': ('left', 'middle'),
    'r': ('left', 'index'), 'f': ('left', 'index'), 'v': ('left', 'index'),
    't': ('left', 'index'), 'g': ('left', 'index'), 'b': ('left', 'index'),
    'y': ('right', 'index'), 'h': ('right', 'index'), 'n': ('right', 'index'),
    'u': ('right', 'index'), 'j': ('right', 'index'), 'm': ('right', 'index'),
    'i': ('right', 'middle'), 'k': ('right', 'middle'),
    'o': ('right', 'ring'), 'l': ('right', 'ring'),
    'p': ('right', 'pinky'),
}

# Multipliers on the delay before a letter. Switching hands lets one hand get
# ready while the other types, so it is a little faster. Two different keys on
# the same finger cannot overlap, so they are a little slower.
HAND_SWITCH_MULTIPLIER = 0.92
SAME_FINGER_MULTIPLIER = 1.18


def get_finger_timing_multiplier(prev_char, char):
    """Returns the delay multiplier for typing `char` right after `prev_char`.
    1.0 means no change. Only letter-to-letter pairs change: spaces, digits,
    punctuation, and a repeated letter (like the double l in "all") stay 1.0."""
    if not prev_char or not char:
        return 1.0
    prev_letter = prev_char[-1].lower()
    letter = char[0].lower()
    if prev_letter == letter:
        return 1.0
    prev_finger = FINGER_FOR_LETTER.get(prev_letter)
    this_finger = FINGER_FOR_LETTER.get(letter)
    if prev_finger is None or this_finger is None:
        return 1.0
    if prev_finger[0] != this_finger[0]:
        return HAND_SWITCH_MULTIPLIER
    if prev_finger == this_finger:
        return SAME_FINGER_MULTIPLIER
    return 1.0


def _build_finger_pair_shifts():
    """Builds a lookup from a two-letter pair to how far its multiplier is from
    1.0. The 16 common pairs are left out because they already type at their
    own fast fixed speed. Used by the time estimate so it stays quick."""
    shifts = {}
    for first in FINGER_FOR_LETTER:
        for second in FINGER_FOR_LETTER:
            pair = first + second
            if pair in COMMON_PAIRS:
                continue
            shift = get_finger_timing_multiplier(first, second) - 1.0
            if shift != 0.0:
                shifts[pair] = shift
    return shifts


FINGER_PAIR_SHIFTS = _build_finger_pair_shifts()


def estimate_finger_timing_shift(text):
    """Adds up how much the finger and hand timing changes the typing time of
    `text`, counted in average per-character delays (negative means faster)."""
    lowered = text.lower()
    return sum(FINGER_PAIR_SHIFTS.get(first + second, 0.0)
               for first, second in zip(lowered, lowered[1:]))

# --------------------------------------------
# QWERTY PHYSICAL ADJACENCY (for realistic single-letter typos)
# --------------------------------------------
QWERTY_ADJACENCY = {
    'q': 'wa', 'w': 'qeas', 'e': 'wrds', 'r': 'etdf', 't': 'ryfg', 'y': 'tugh',
    'u': 'yihj', 'i': 'uojk', 'o': 'ipkl', 'p': 'ol',
    'a': 'qwsz', 's': 'awedxz', 'd': 'serfcx', 'f': 'drtgvc', 'g': 'ftyhbv',
    'h': 'gyujnb', 'j': 'huikmn', 'k': 'jiolm', 'l': 'kop',
    'z': 'asx', 'x': 'zsdc', 'c': 'xdfv', 'v': 'cfgb', 'b': 'vghn', 'n': 'bhjm', 'm': 'njk',
}

# --------------------------------------------
# PROTECTED-TEXT MARKERS: [[exact text]]
# --------------------------------------------
# Wrapping a span of the input file in [[double brackets]] marks it
# as text that must always type out exactly right -- no adjacent-key
# typos, no sentence reworks. The brackets themselves are stripped
# and never typed; only the text inside them is, at the normal
# per-character delay. Nested brackets aren't supported.
PROTECTED_MARKER_PATTERN = re.compile(r"\[\[(.*?)\]\]", re.DOTALL)


def parse_protected_spans(raw_text):
    """Strips [[...]] markers out of raw_text, returning the clean
    text that will actually be typed, plus a list of (start, end)
    character ranges (indexing into the CLEAN text) that came from
    inside a marker and must be typed exactly."""
    clean_parts = []
    protected_ranges = []
    last_end = 0
    clean_pos = 0

    for match in PROTECTED_MARKER_PATTERN.finditer(raw_text):
        before = raw_text[last_end:match.start()]
        clean_parts.append(before)
        clean_pos += len(before)

        inner = match.group(1)
        protected_ranges.append((clean_pos, clean_pos + len(inner)))
        clean_parts.append(inner)
        clean_pos += len(inner)

        last_end = match.end()

    clean_parts.append(raw_text[last_end:])
    return "".join(clean_parts), protected_ranges


def build_protection_mask(length, protected_ranges):
    mask = [False] * length
    for start, end in protected_ranges:
        for i in range(start, min(end, length)):
            mask[i] = True
    return mask


# --------------------------------------------
# INLINE PAUSE MARKERS: {{pause:N}}
# --------------------------------------------
# Writing {{pause:5}} anywhere in the input file makes Amanuensis wait
# 5 seconds at that exact spot. The marker itself is never typed.
# Whole numbers and decimals are allowed (e.g. {{pause:2.5}}). Values
# above PAUSE_MARKER_MAX_SECONDS are clamped. Anything that looks like
# a pause marker but has a bad value (e.g. {{pause:abc}}) is NOT removed:
# it is typed as normal text, and a warning is shown before typing starts.
#
# Pause markers are removed first, then [[protected markers]] are
# parsed. Each pause is stored as a position in the FINAL clean text
# (the exact text that gets typed), meaning "wait before typing the
# character at this index". That keeps pauses lined up with resume
# checkpoints, which also count positions in the clean text.
PAUSE_MARKER_MAX_SECONDS = 600.0
PAUSE_MARKER_PATTERN = re.compile(r"\{\{\s*pause\b([^{}]*)\}\}", re.IGNORECASE)
PAUSE_MARKER_VALUE_PATTERN = re.compile(r"^\s*:\s*([0-9]+(?:\.[0-9]+)?|\.[0-9]+)\s*$")


def strip_pause_markers(raw_text):
    """Removes valid {{pause:N}} markers from raw_text.

    Returns (stripped_text, pause_positions, invalid_markers, clamped_markers):
    - stripped_text: raw_text with every valid pause marker removed.
    - pause_positions: dict of {index into stripped_text: total seconds}.
      A pause at index i means "wait before typing the character at i";
      i == len(stripped_text) means "wait after the last character".
    - invalid_markers: marker texts with a bad value. These are left in
      the text untouched, so they get typed as normal text.
    - clamped_markers: marker texts whose value was over the maximum and
      was shortened to PAUSE_MARKER_MAX_SECONDS.
    """
    kept_parts = []
    pause_positions = {}
    invalid_markers = []
    clamped_markers = []
    last_end = 0
    stripped_length = 0

    for match in PAUSE_MARKER_PATTERN.finditer(raw_text):
        value_match = PAUSE_MARKER_VALUE_PATTERN.match(match.group(1))
        if value_match is None:
            # Bad value: leave it in the text (last_end is not moved).
            invalid_markers.append(match.group(0))
            continue

        before = raw_text[last_end:match.start()]
        kept_parts.append(before)
        stripped_length += len(before)

        seconds = float(value_match.group(1))
        if seconds > PAUSE_MARKER_MAX_SECONDS:
            clamped_markers.append(match.group(0))
            seconds = PAUSE_MARKER_MAX_SECONDS
        if seconds > 0:
            pause_positions[stripped_length] = pause_positions.get(stripped_length, 0.0) + seconds

        last_end = match.end()

    kept_parts.append(raw_text[last_end:])
    return "".join(kept_parts), pause_positions, invalid_markers, clamped_markers


# --------------------------------------------
# INLINE FORMATTING MARKERS: [[b]]...[[/b]] and [[i]]...[[/i]] (Update #29)
# --------------------------------------------
# [[b]] starts bold and [[/b]] ends it. [[i]] and [[/i]] do the same for
# italic (any capitals work). The markers are never typed. Where one sits,
# Amanuensis presses Ctrl+B or Ctrl+I, which TOGGLES the style in Google Docs
# and Word, so a valid pair turns it on and then off again.
#
# These are removed BEFORE [[protected markers]] are read, because [[b]] would
# otherwise look like protected text. That makes these four spellings
# reserved. Each marker is stored as a position in the FINAL clean text,
# meaning "press the shortcut before typing the character at this index",
# exactly like pauses, so it stays in step with resume checkpoints.
#
# A marker with no partner is never typed as text. A stray [[/b]] or a
# repeated [[b]] is ignored, and a style still open at the end of the file is
# closed after the last character. Each case is reported before typing starts.
FORMAT_MARKER_PATTERN = re.compile(r"\[\[(/?)([bi])\]\]", re.IGNORECASE)
FORMAT_KIND_FOR_LETTER = {"b": "bold", "i": "italic"}
FORMAT_SHORTCUT_LETTER = {"bold": "b", "italic": "i"}
FORMAT_KIND_ORDER = ("bold", "italic")
# Average time of one shortcut press (reaching for the keys, then pressing).
FORMAT_TOGGLE_ESTIMATE_SECONDS = 0.33


def shift_position_through_removals(position, removed_spans):
    """Converts an index in a text to the matching index after the sorted,
    non-overlapping (start, end) spans were cut out of it."""
    shift = 0
    for span_start, span_end in removed_spans:
        if span_start >= position:
            break
        shift += min(span_end, position) - span_start
    return position - shift


def normalize_format_map(format_map):
    """Cancels a style that is toggled twice at the very same spot (it would
    press the shortcut twice for no change) and drops empty entries."""
    cleaned = {}
    for position, kinds in format_map.items():
        kept = [kind for kind in dict.fromkeys(kinds) if kinds.count(kind) % 2 == 1]
        if kept:
            cleaned[position] = kept
    return cleaned


def strip_format_markers(raw_text):
    """Removes [[b]], [[/b]], [[i]], and [[/i]] from raw_text.

    Returns (stripped_text, format_positions, removed_spans, problems):
    - stripped_text: raw_text with every formatting marker removed.
    - format_positions: {index into stripped_text: [style, ...]} where each
      style is "bold" or "italic" and means "press that shortcut before the
      character at this index". An index equal to len(stripped_text) is
      after the last character (this also holds the automatic close of any
      style left open).
    - removed_spans: (start, end) of each marker in raw_text, so other
      positions can be shifted with shift_position_through_removals().
    - problems: plain-English notes about markers that have no partner."""
    kept_parts = []
    format_positions = {}
    removed_spans = []
    problems = []
    open_markers = {}  # style -> the marker text that opened it
    last_end = 0
    stripped_length = 0

    for match in FORMAT_MARKER_PATTERN.finditer(raw_text):
        before = raw_text[last_end:match.start()]
        kept_parts.append(before)
        stripped_length += len(before)
        removed_spans.append((match.start(), match.end()))
        last_end = match.end()

        marker_text = match.group(0)
        is_closing = match.group(1) == "/"
        kind = FORMAT_KIND_FOR_LETTER[match.group(2).lower()]
        if is_closing:
            if kind not in open_markers:
                problems.append(f"{marker_text}  (no matching opening marker, ignored)")
                continue
            del open_markers[kind]
        else:
            if kind in open_markers:
                problems.append(f"{marker_text}  ({kind} is already on, ignored)")
                continue
            open_markers[kind] = marker_text
        format_positions.setdefault(stripped_length, []).append(kind)

    tail = raw_text[last_end:]
    kept_parts.append(tail)
    stripped_length += len(tail)

    for kind in FORMAT_KIND_ORDER:
        if kind in open_markers:
            problems.append(f"{open_markers[kind]}  (never closed, will be turned off after the last character)")
            format_positions.setdefault(stripped_length, []).append(kind)

    return "".join(kept_parts), normalize_format_map(format_positions), removed_spans, problems


def parse_input_markers(raw_text, enable_pause_markers=True):
    """Parses every marker type in the input file in one place.

    Returns (clean_text, protected_ranges, pause_map, format_map, invalid_markers, clamped_markers):
    - clean_text: the exact text that will be typed (no markers left in it).
    - protected_ranges: (start, end) ranges in clean_text for [[protected text]].
    - pause_map: {index into clean_text: seconds to wait before typing that
      character}. An index equal to len(clean_text) is a pause at the very end.
    - format_map: {index into clean_text: ["bold" or "italic", ...]} shortcuts
      to press before typing that character (see strip_format_markers()).
    When enable_pause_markers is False, {{pause:N}} is left in the text and
    typed like any other text. Formatting markers are always removed from the
    text (the caller decides whether to press the shortcuts)."""
    if enable_pause_markers:
        stripped, stripped_pauses, invalid_markers, clamped_markers = strip_pause_markers(raw_text)
    else:
        stripped, stripped_pauses, invalid_markers, clamped_markers = raw_text, {}, [], []

    # Formatting markers go before [[protected markers]] are read. Pause
    # positions so far point into the text with the formatting markers still
    # in it, so shift them to match.
    stripped, format_positions, format_removed_spans, _format_problems = strip_format_markers(stripped)
    if format_removed_spans and stripped_pauses:
        shifted_pauses = {}
        for position, seconds in stripped_pauses.items():
            new_position = shift_position_through_removals(position, format_removed_spans)
            shifted_pauses[new_position] = shifted_pauses.get(new_position, 0.0) + seconds
        stripped_pauses = shifted_pauses

    clean_text, protected_ranges = parse_protected_spans(stripped)

    # Pause and format positions so far point into `stripped`, which still has
    # the [[ and ]] characters in it. Shift each one left by however many
    # bracket characters sit before it, so it points into clean_text.
    pause_map = {}
    format_map = {}
    if stripped_pauses or format_positions:
        bracket_spans = []
        for match in PROTECTED_MARKER_PATTERN.finditer(stripped):
            bracket_spans.append((match.start(), match.start() + 2))
            bracket_spans.append((match.end() - 2, match.end()))

        for position, seconds in stripped_pauses.items():
            clean_position = shift_position_through_removals(position, bracket_spans)
            pause_map[clean_position] = pause_map.get(clean_position, 0.0) + seconds
        for position, kinds in format_positions.items():
            clean_position = shift_position_through_removals(position, bracket_spans)
            format_map.setdefault(clean_position, []).extend(kinds)

    return clean_text, protected_ranges, pause_map, normalize_format_map(format_map), invalid_markers, clamped_markers


# --------------------------------------------
# SMART TEXT FIXES (Update #22)
# --------------------------------------------
# A local, rule-based "proofreading" pass over the text that is about to
# be typed. It never uses AI or the internet: every decision comes from
# the plain rules below, so the same text and settings always produce
# exactly the same result.
#
# What it can fix (each group can be turned on or off):
#   spacing         extra spaces, trailing spaces, a space before , . ; : ! ?
#                   and a missing space after , . ; ! ?
#   punctuation     a missing comma after openers like "However", a missing
#                   period at the end of a paragraph, doubled punctuation
#   capitalization  the first letter of a sentence, and a lowercase "i"
#   repeated_words  an accidental repeat such as "the the"
#
# Safety rules (do not weaken these):
#   * Text inside [[protected markers]] is NEVER changed. A fix is skipped if
#     it would touch a protected character or squeeze in right next to one.
#   * Rules are deliberately cautious. When a spot looks like a web address,
#     email, file path, code, an abbreviation or a list number, it is left alone.
#   * Rules are written for English punctuation and spacing.
#   * Everything is worked out BEFORE typing starts and is fully repeatable,
#     so resume-from-checkpoint positions stay in line with the fixed text.
#   * If anything unexpected goes wrong, prepare_input_text() falls back to
#     the original text instead of risking a wrong result.
#
# Fixes are planned as SmartFix edits against the clean text (markers already
# removed). They are applied together, and every position that points into
# the text (protected ranges, pause markers) is shifted to match.
# During typing, a fix can also be "acted out": the typer first makes the
# slip (for example types a stray space), pauses as if noticing it, backspaces
# it, and carries on. The final text on screen is the same either way.

SMART_FIX_LABELS = {
    "extra_space": "Removed an extra space",
    "trailing_space": "Removed a space at the end of a line",
    "space_before_punct": "Removed a space before punctuation",
    "missing_space": "Added a missing space after punctuation",
    "missing_comma": "Added a missing comma after an opening word",
    "missing_period": "Added a missing period at the end of a paragraph",
    "double_punct": "Removed doubled punctuation",
    "capitalization": "Fixed a capital letter",
    "repeated_word": "Removed a repeated word",
}

# Characters that make a chunk of text look like a web address, email, file
# path, or code. Punctuation rules leave those chunks alone.
SMART_TECHNICAL_CHARS = set("/\\=@&#%_~^|<>{}")
SMART_TRAILING_PUNCT = ",.;:!?)\"'\u201d\u2019"

# Words that are allowed to appear twice in a row ("had had", "that that").
SMART_REPEAT_OK = {"had", "that", "is", "do", "no", "very", "so", "ha", "oh", "bye", "yeah", "knock", "blah"}

# Sentence-opening words and phrases that normally take a comma after them.
SMART_INTRO_PHRASES = (
    "however", "therefore", "moreover", "furthermore", "additionally", "consequently",
    "nevertheless", "nonetheless", "meanwhile", "unfortunately", "fortunately", "honestly",
    "basically", "overall", "ultimately", "similarly", "likewise", "specifically",
    "generally", "accordingly", "otherwise", "instead", "finally", "firstly", "secondly",
    "thirdly", "lastly", "initially", "eventually", "obviously",
    "for example", "for instance", "in addition", "in conclusion", "in summary",
    "in fact", "in general", "in particular", "as a result", "on the other hand",
    "of course", "in other words", "by the way", "at the same time", "after all",
    "in the meantime",
)
SMART_INTRO_PATTERN = re.compile(
    "(?:" + "|".join(sorted((re.escape(p) for p in SMART_INTRO_PHRASES), key=len, reverse=True)) + ")(?= )",
    re.IGNORECASE,
)
# A comma is only added when the word AFTER the opener is one of these. That
# keeps "However hard I try" and "Overall quality" (no comma needed) alone.
SMART_INTRO_NEXT_WORDS = {
    "i", "we", "you", "he", "she", "they", "it", "the", "a", "an", "this", "that",
    "these", "those", "there", "my", "our", "your", "his", "her", "their", "its",
    "some", "many", "most", "all", "one", "no", "not", "every", "each", "if",
    "when", "as", "in", "on", "at", "for", "with", "after", "before", "because",
    "since", "although", "while",
}

# Words that end in a period without ending the sentence.
SMART_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "inc", "ltd", "co",
    "no", "fig", "approx", "dept", "est", "gen", "sen", "rep", "vol", "jan", "feb",
    "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}

# Lowercase roman numerals, which start list items like "ii. second point".
SMART_ROMAN_NUMERALS = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}

SMART_RUN_OF_SPACES = re.compile(r" +")
SMART_COMMA_RUN = re.compile(r",{2,}|;{2,}")
SMART_TWO_DOTS = re.compile(r"(?<!\.)\.\.(?!\.)")
SMART_SENTENCE_BREAK = re.compile(r"([.!?]+)[\"'\u201d\u2019)\]]*\s+|\n+")
SMART_REPEATED_WORD = re.compile(
    r"(?<![\w'\u2019])([A-Za-z][A-Za-z'\u2019]*)((?: \1)+)(?![\w'\u2019])", re.IGNORECASE
)
SMART_LONE_I = re.compile(r"(?<![\w'\u2019.\-/\\@])i(?=[\s,;!?]|['\u2019][A-Za-z]|$)")
SMART_WORD_LETTERS = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)?")
SMART_NEXT_WORD = re.compile(r"[A-Za-z]+")


class SmartFix:
    """One planned change to the clean text: replace text[start:end] with
    `replacement`. start == end means an insertion; an empty replacement
    means a deletion. `wrong_text` is what the typer briefly types by
    "mistake" when it acts the fix out (None means no visible version)."""

    __slots__ = ("kind", "start", "end", "replacement", "wrong_text")

    def __init__(self, kind, start, end, replacement, wrong_text=None):
        self.kind = kind
        self.start = start
        self.end = end
        self.replacement = replacement
        self.wrong_text = wrong_text


class PreparedText:
    """Everything type_text() and the estimate/resume code need to know
    about one input file, after markers and smart fixes are handled.
    All positions refer to clean_text (the exact text that gets typed)."""

    def __init__(self, clean_text, protected_ranges, pause_map, format_map, invalid_markers, clamped_markers):
        self.clean_text = clean_text
        self.protected_ranges = protected_ranges
        self.pause_map = pause_map
        self.format_map = format_map    # {index in clean_text: ["bold" / "italic", ...]} (Update #29)
        self.invalid_markers = invalid_markers
        self.clamped_markers = clamped_markers
        self.source_text = clean_text   # the text before smart fixes
        self.fixes = []                 # SmartFix list (positions in source_text)
        self.fix_actions = {}           # {index in clean_text: [(kind, wrong_text), ...]}


def get_smart_fix_options(settings, ignore_master=False):
    """Returns which fix groups are on, or None when smart fixes are off
    (or every group is off). ignore_master=True is used by the review
    window, which shows what WOULD change even while the master switch is off."""
    if not ignore_master and not settings.get("enable_smart_fixes", False):
        return None
    options = {
        "spacing": bool(settings.get("smart_fix_spacing", True)),
        "punctuation": bool(settings.get("smart_fix_punctuation", True)),
        "capitalization": bool(settings.get("smart_fix_capitalization", True)),
        "repeated_words": bool(settings.get("smart_fix_repeated_words", True)),
    }
    if not any(options.values()):
        return None
    return options


def _smart_token_around(text, index):
    """Returns the chunk of text (no whitespace inside) that contains the
    character at `index`."""
    start = index
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = index
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[start:end]


def _smart_looks_technical(token):
    """True for chunks that look like web addresses, emails, paths or code."""
    lowered = token.lower()
    if lowered.startswith(("www.", "http")):
        return True
    return any(char in SMART_TECHNICAL_CHARS for char in token)


def _smart_space_before_punct_ok(text, run_start, run_end):
    """Decides whether a run of spaces right before , . ; : ! ? is a stray
    space (True) or something to leave alone, like an ellipsis, a smiley,
    or a file extension (False)."""
    previous_char = text[run_start - 1]
    if not (previous_char.isalnum() or previous_char in "\"'\u201d\u2019)]"):
        return False
    punct = text[run_end]
    after = text[run_end + 1] if run_end + 1 < len(text) else ""
    if after and not (after.isspace() or after in "\"'\u201d\u2019"):
        return False
    if punct == "." and after == ".":
        return False
    return True


def _smart_comma_glued_to_next_word(text, run_start, run_end):
    """True for 'word ,next': a comma with a space before it and a letter
    right after it, in text that is not a web address, path, or code."""
    if not text[run_start - 1].isalpha():
        return False
    if run_end + 1 >= len(text) or not text[run_end + 1].isalpha():
        return False
    return not _smart_looks_technical(_smart_token_around(text, run_end))


def _find_space_fixes(text):
    fixes = []
    for match in SMART_RUN_OF_SPACES.finditer(text):
        start, end = match.start(), match.end()
        if start == 0 or text[start - 1] == "\n":
            continue  # indentation is left alone
        next_char = text[end] if end < len(text) else ""
        if next_char == "" or next_char == "\n":
            fixes.append(SmartFix("trailing_space", start, end, "", text[start:end][:3]))
        elif next_char in ",.;:!?" and _smart_space_before_punct_ok(text, start, end):
            fixes.append(SmartFix("space_before_punct", start, end, "", text[start:end][:3]))
        elif next_char == "," and _smart_comma_glued_to_next_word(text, start, end):
            # "word ,next" becomes "word, next": drop the stray space and add the missing one.
            fixes.append(SmartFix("space_before_punct", start, end, "", text[start:end][:3]))
            fixes.append(SmartFix("missing_space", end + 1, end + 1, " ", text[end + 1]))
        elif end - start >= 2:
            fixes.append(SmartFix("extra_space", start + 1, end, "", text[start + 1:end][:3]))
    return fixes


def _find_missing_space_fixes(text):
    fixes = []
    for index in range(1, len(text) - 1):
        char = text[index]
        if char not in ",.;!?":
            continue
        previous_char = text[index - 1]
        next_char = text[index + 1]
        if not (previous_char.isalpha() and next_char.isalpha()):
            continue
        token = _smart_token_around(text, index)
        if _smart_looks_technical(token):
            continue
        if char == ".":
            # Only "word.Name" (a lowercase word, then a capitalized word).
            if not previous_char.islower():
                continue
            if not (next_char.isupper() and index + 2 < len(text) and text[index + 2].islower()):
                continue
            if token.rstrip(SMART_TRAILING_PUNCT).count(".") > 1:
                continue  # initials or dotted names such as U.S.A
            run_start = index - 1
            while run_start >= 0 and text[run_start].isalpha():
                run_start -= 1
            if index - 1 - run_start < 2:
                continue  # a single letter before the dot, like an initial
            if run_start >= 0 and text[run_start].isdigit():
                continue
        fixes.append(SmartFix("missing_space", index + 1, index + 1, " ", next_char))
    return fixes


def _find_double_punct_fixes(text):
    fixes = []
    for match in SMART_COMMA_RUN.finditer(text):
        fixes.append(SmartFix("double_punct", match.start() + 1, match.end(), "", match.group(0)[1:3]))
    for match in SMART_TWO_DOTS.finditer(text):
        if _smart_looks_technical(_smart_token_around(text, match.start())):
            continue
        fixes.append(SmartFix("double_punct", match.start() + 1, match.end(), "", "."))
    return fixes


def _smart_sentence_starts(text, include_glued=False):
    """Returns (index, after_period, previous_token) for the start of the
    text, every line, and every sentence. after_period is the sentence-ending
    punctuation that came just before (or "" for the start of a line/text).
    With include_glued=True, a sentence that is glued to the one before it
    ("done.Then") also counts, because the space fix is about to separate them."""
    starts = [(0, "", "")]
    for match in SMART_SENTENCE_BREAK.finditer(text):
        punct = match.group(1) or ""
        previous_token = ""
        if punct:
            before = text[max(0, match.start() - 40):match.start()].split()
            previous_token = before[-1] if before else ""
        starts.append((match.end(), punct, previous_token))
    if include_glued:
        for fix in _find_missing_space_fixes(text):
            punct = text[fix.start - 1]
            if punct not in ".!?":
                continue
            before = text[max(0, fix.start - 41):fix.start - 1].split()
            starts.append((fix.start, punct, before[-1] if before else ""))
    return starts


def _smart_skip_openers(text, index):
    while index < len(text) and text[index] in "\"'\u201c\u2018([":
        index += 1
    return index


def _find_intro_comma_fixes(text, include_glued=False):
    fixes = []
    seen = set()
    for raw_index, _punct, _previous in _smart_sentence_starts(text, include_glued):
        index = _smart_skip_openers(text, raw_index)
        if index in seen or index >= len(text):
            continue
        seen.add(index)
        match = SMART_INTRO_PATTERN.match(text, index)
        if match is None:
            continue
        next_word = SMART_NEXT_WORD.match(text, match.end() + 1)
        if next_word is None or next_word.group(0).lower() not in SMART_INTRO_NEXT_WORDS:
            continue
        fixes.append(SmartFix("missing_comma", match.end(), match.end(), ",", " "))
    return fixes


def _find_final_period_fixes(text):
    """Adds a period to a long paragraph that has none. To avoid breaking
    text that was hard-wrapped (lines cut mid-sentence), only a line that
    is followed by a blank line, or is the last line of the file, counts."""
    fixes = []
    lines = text.split("\n")
    position = 0
    for line_number, line in enumerate(lines):
        line_start = position
        position += len(line) + 1
        trimmed = line.rstrip(" ")
        if not trimmed or len(trimmed.split()) < 8:
            continue
        is_last_line = line_number == len(lines) - 1
        if not is_last_line and lines[line_number + 1].strip():
            continue
        first_char = trimmed.lstrip(" ")[0]
        if not first_char.isalpha() or not trimmed[-1].isalnum() or trimmed.isupper():
            continue
        if _smart_looks_technical(trimmed.split()[-1]):
            continue
        insert_at = line_start + len(trimmed)
        fixes.append(SmartFix("missing_period", insert_at, insert_at, ".", None))
    return fixes


def _smart_starts_plain_word(text, index):
    """True when a plain all-lowercase word starts at `index`, followed by a
    space, the end, or ordinary punctuation. This leaves alone brand names
    (eBay), web addresses, code, file names, and list markers such as
    "a." or "ii."."""
    word_match = SMART_WORD_LETTERS.match(text, index)
    if word_match is None:
        return False
    word = word_match.group(0)
    if word != word.lower():
        return False
    after = text[word_match.end()] if word_match.end() < len(text) else ""
    after_after = text[word_match.end() + 1] if word_match.end() + 1 < len(text) else ""
    if after == ".":
        if len(word) == 1 or word in SMART_ROMAN_NUMERALS:
            return False  # list marker or initial, like "a." or "ii."
        if after_after.isalnum():
            return False  # a dotted name such as e.g. or example.com
    elif after == "-":
        if not after_after.isalpha():
            return False
    elif after and not (after.isspace() or after in ",;:!?)\"'\u201d\u2019"):
        return False
    return not _smart_looks_technical(_smart_token_around(text, index))


def _find_capitalization_fixes(text, include_glued=False):
    fixes = []
    for raw_index, punct, previous_token in _smart_sentence_starts(text, include_glued):
        index = _smart_skip_openers(text, raw_index)
        if index >= len(text) or not text[index].islower():
            continue
        if punct:
            if "." in punct and punct != ".":
                continue  # ellipsis: lowercase after it is usually on purpose
            lowered_token = previous_token.lower().strip("(\"'\u201c\u2018[")
            if punct == ".":
                if (lowered_token in SMART_ABBREVIATIONS or lowered_token in SMART_ROMAN_NUMERALS
                        or len(lowered_token) == 1 or "." in lowered_token):
                    continue
                if lowered_token.isdigit() and len(lowered_token) <= 2:
                    continue  # list numbers like "1. item"
                if any(char.isdigit() for char in lowered_token) and not lowered_token.isdigit():
                    continue
        elif raw_index > 0:
            # A new line (not a new sentence). Only capitalize when the line
            # before it ended a sentence or was blank, so hard-wrapped text
            # that continues mid-sentence is never touched.
            before_break = text[:raw_index].rstrip("\n")
            ended_sentence = before_break == "" or text[:raw_index].endswith("\n\n") or \
                before_break.rstrip("\"'\u201d\u2019)]")[-1:] in (".", "!", "?")
            if not ended_sentence:
                continue
        if not _smart_starts_plain_word(text, index):
            continue
        fixes.append(SmartFix("capitalization", index, index + 1, text[index].upper(), text[index]))

    for match in SMART_LONE_I.finditer(text):
        if _smart_looks_technical(_smart_token_around(text, match.start())):
            continue
        fixes.append(SmartFix("capitalization", match.start(), match.start() + 1, "I", "i"))
    return fixes


def _find_repeated_word_fixes(text):
    fixes = []
    for match in SMART_REPEATED_WORD.finditer(text):
        word = match.group(1)
        if word.lower() in SMART_REPEAT_OK:
            continue
        if _smart_looks_technical(_smart_token_around(text, match.start())):
            continue
        repeat_start = match.end(1)
        fixes.append(SmartFix("repeated_word", repeat_start, match.end(2), "", " " + word))
    return fixes


def _smart_fix_is_allowed(fix, protection_mask):
    """A fix may never delete or replace a protected character, and an
    insertion may not sit directly beside one."""
    length = len(protection_mask)
    if fix.start == fix.end:
        if fix.start > 0 and protection_mask[fix.start - 1]:
            return False
        if fix.start < length and protection_mask[fix.start]:
            return False
        return True
    return not any(protection_mask[fix.start:fix.end])


def find_smart_fixes(text, protection_mask, options):
    """Plans every fix for `text`. Returns a list of SmartFix edits, sorted by
    position, none overlapping each other, none touching protected text."""
    planned = []
    if options.get("spacing"):
        planned += _find_space_fixes(text)
        planned += _find_missing_space_fixes(text)
    if options.get("punctuation"):
        planned += _find_intro_comma_fixes(text, options.get("spacing", False))
        planned += _find_final_period_fixes(text)
        planned += _find_double_punct_fixes(text)
    if options.get("capitalization"):
        planned += _find_capitalization_fixes(text, options.get("spacing", False))
    if options.get("repeated_words"):
        planned += _find_repeated_word_fixes(text)

    planned.sort(key=lambda fix: (fix.start, fix.end))
    accepted = []
    for fix in planned:
        if not _smart_fix_is_allowed(fix, protection_mask):
            continue
        if accepted:
            last = accepted[-1]
            if fix.start < last.end:
                continue  # overlaps an earlier fix
            if fix.start == fix.end == last.start == last.end:
                continue  # two insertions at the very same spot
        accepted.append(fix)
    return accepted


def apply_smart_fixes(text, fixes):
    """Applies the fixes (sorted, non-overlapping) to `text`. Returns
    (new_text, new_starts) where new_starts[i] is the index in new_text
    where fixes[i]'s replacement begins."""
    pieces = []
    new_starts = []
    position = 0
    shift = 0
    for fix in fixes:
        pieces.append(text[position:fix.start])
        new_starts.append(fix.start + shift)
        pieces.append(fix.replacement)
        shift += len(fix.replacement) - (fix.end - fix.start)
        position = fix.end
    pieces.append(text[position:])
    return "".join(pieces), new_starts


def map_position_through_fixes(position, fixes):
    """Converts an index in the old text to the matching index in the fixed
    text. A position that sat inside deleted text moves to where that
    deleted text used to be."""
    shift = 0
    for fix in fixes:
        if fix.end <= position:
            shift += len(fix.replacement) - (fix.end - fix.start)
        elif fix.start < position:
            return fix.start + shift + len(fix.replacement)
        else:
            break
    return position + shift


def build_fix_actions(fixes, new_starts):
    """Builds {index in fixed text: [(kind, wrong_text), ...]}. When the
    typer reaches that index it may act the fix out by typing wrong_text,
    pausing, and backspacing it before typing the real character."""
    actions = {}
    for fix, new_start in zip(fixes, new_starts):
        if not fix.wrong_text:
            continue
        actions.setdefault(new_start, []).append((fix.kind, fix.wrong_text))
    return actions


def summarize_smart_fixes(fixes):
    """A short, text-free summary for the session log, such as
    '3 fixes: capitalization=2, extra_space=1'."""
    counts = {}
    for fix in fixes:
        counts[fix.kind] = counts.get(fix.kind, 0) + 1
    parts = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    return f"{len(fixes)} fixes: {parts}" if fixes else "no fixes"


def prepare_input_text(raw_text, settings):
    """Turns the raw file text into everything needed to type it: strips
    the markers, then (when smart fixes are on) applies them. This is the
    one place every caller goes through, so typing, estimates, and resume
    checks always agree on exactly what the text is."""
    clean_text, protected_ranges, pause_map, format_map, invalid_markers, clamped_markers = parse_input_markers(
        raw_text, settings.get("enable_pause_markers", True)
    )
    if not settings.get("enable_format_markers", True):
        # The markers are still removed from the text, but no shortcuts get pressed.
        format_map = {}
    prepared = PreparedText(clean_text, protected_ranges, pause_map, format_map, invalid_markers, clamped_markers)

    options = get_smart_fix_options(settings)
    if options is None or not clean_text:
        return prepared

    try:
        protection_mask = build_protection_mask(len(clean_text), protected_ranges)
        fixes = find_smart_fixes(clean_text, protection_mask, options)
        if not fixes:
            return prepared

        new_text, new_starts = apply_smart_fixes(clean_text, fixes)

        new_ranges = []
        for range_start, range_end in protected_ranges:
            new_start = map_position_through_fixes(range_start, fixes)
            new_end = map_position_through_fixes(range_end, fixes)
            # Safety net: protected text must come out exactly as it went in.
            if clean_text[range_start:range_end] != new_text[new_start:new_end]:
                return prepared
            new_ranges.append((new_start, new_end))

        new_pause_map = {}
        for pause_position, seconds in pause_map.items():
            new_position = map_position_through_fixes(pause_position, fixes)
            new_pause_map[new_position] = new_pause_map.get(new_position, 0.0) + seconds

        new_format_map = {}
        for format_position, kinds in format_map.items():
            new_position = map_position_through_fixes(format_position, fixes)
            new_format_map.setdefault(new_position, []).extend(kinds)

        prepared.clean_text = new_text
        prepared.protected_ranges = new_ranges
        prepared.pause_map = new_pause_map
        prepared.format_map = normalize_format_map(new_format_map)
        prepared.fixes = fixes
        prepared.fix_actions = build_fix_actions(fixes, new_starts)
    except Exception:
        # Default to safety: if the fixer ever fails, type the original text.
        return PreparedText(clean_text, protected_ranges, pause_map, format_map, invalid_markers, clamped_markers)
    return prepared


def describe_smart_fix(source_text, fix, context=24):
    """Returns (line_number, label, before_snippet, after_snippet) for the
    review window. The changed part is wrapped in angle quotes, spaces inside it
    are shown as dots, and line breaks as arrows, so tiny changes are visible."""
    window_start = max(0, fix.start - context)
    window_end = min(len(source_text), fix.end + context)

    def show(piece):
        return piece.replace("\n", "\u21b5")

    def show_changed(piece):
        return piece.replace(" ", "\u00b7").replace("\n", "\u21b5")

    head = show(source_text[window_start:fix.start])
    tail = show(source_text[fix.end:window_end])
    before = f"{head}\u00ab{show_changed(source_text[fix.start:fix.end])}\u00bb{tail}"
    after = f"{head}\u00ab{show_changed(fix.replacement)}\u00bb{tail}"
    line_number = source_text.count("\n", 0, fix.start) + 1
    return line_number, SMART_FIX_LABELS.get(fix.kind, fix.kind), before, after


# --------------------------------------------
# LEAVE MISTAKES FOR ME (Update #24)
# --------------------------------------------
# An optional mode that purposely leaves a few mistakes in the typed text so
# the person can go back and fix them by hand. It is the opposite of Smart
# Text Fixes. The mistakes are chosen BEFORE typing starts. Each one is a
# small edit of the typed text (for example "type 'teh' instead of 'the'"),
# and the clean text itself never changes, so resume positions, protected
# text, and pause markers all stay lined up.
#
# Kinds of mistakes (three on/off groups):
#   typos         wrong letter (a neighboring key), swapped letters, doubled
#                 letter, missing letter, and two words run together
#   commas        a comma that is left out
#   punctuation   an end-of-sentence period, ? or !, or an apostrophe left out
#
# Safety rules (do not weaken these):
#   * Text inside [[protected markers]] is NEVER touched, and neither is a
#     word that has protected text in it.
#   * Web addresses, emails, paths, code, anything with a digit, ALL-CAPS
#     words, and abbreviations are skipped.
#   * A mistake never lands where a pause marker or a smart-fix "noticed a
#     slip" action sits inside it.
#   * Once a mistake is typed, nothing in the typer deletes or retypes it.
#     Sentence reworks skip the words around it (see rework_hold in
#     type_text), and the regular typos only ever backspace their own
#     characters.
#   * Every mistake that is typed is recorded in state.left_mistakes with its
#     line number and a before/after snippet for the report window.

LEFT_MISTAKE_LABELS = {
    "wrong_letter": "Wrong letter (a neighboring key)",
    "swapped_letters": "Swapped letters",
    "doubled_letter": "Doubled letter",
    "missing_letter": "Missing letter",
    "run_together": "Missing space (two words run together)",
    "missing_comma": "Missing comma",
    "missing_period": "Missing end punctuation",
    "missing_apostrophe": "Missing apostrophe",
}
LEFT_MISTAKE_GROUPS = ("typos", "commas", "punctuation")
LEFT_MISTAKE_DEFAULT_COUNT = 3
LEFT_MISTAKE_MAX_COUNT = 200
LEFT_MISTAKE_CONTEXT = 24      # characters of text shown on each side in the report
LEFT_MISTAKE_MIN_GAP = 6       # closest two left mistakes may ever be (in characters)


def get_left_mistake_options(settings):
    """Returns which mistake groups are on, or None when leaving mistakes is
    off (or every group is off)."""
    if not settings.get("enable_leave_mistakes", False):
        return None
    options = {
        "typos": bool(settings.get("leave_mistakes_typos", True)),
        "commas": bool(settings.get("leave_mistakes_commas", True)),
        "punctuation": bool(settings.get("leave_mistakes_punctuation", True)),
    }
    if not any(options.values()):
        return None
    return options


def _left_word_bounds(text, index):
    """Start and end (end is not included) of the run of letters that
    contains text[index]."""
    start = index
    while start > 0 and text[start - 1].isalpha():
        start -= 1
    end = index + 1
    while end < len(text) and text[end].isalpha():
        end += 1
    return start, end


def _left_token_is_plain(text, index, protection_mask):
    """True when the chunk of text around `index` (no whitespace inside) is
    ordinary prose: not a web address, email, path, or code, has no digits,
    and has no protected text in it."""
    start = index
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = index + 1
    while end < len(text) and not text[end].isspace():
        end += 1
    token = text[start:end]
    if _smart_looks_technical(token):
        return False
    if any(char.isdigit() for char in token):
        return False
    return not any(protection_mask[start:end])


def _left_letter_kinds(text, index, protection_mask):
    """Which typo kinds could be left at the letter text[index]. Only the
    inside of ordinary words with 3 or more letters qualifies, never the first
    letter, so the result still looks like a believable slip."""
    word_start, word_end = _left_word_bounds(text, index)
    word_length = word_end - word_start
    if word_length < 3 or index == word_start:
        return []
    if text[word_start:word_end].isupper():
        return []
    if not _left_token_is_plain(text, index, protection_mask):
        return []

    char = text[index]
    previous_char = text[index - 1]
    kinds = []
    if char.lower() in QWERTY_ADJACENCY:
        kinds.append("wrong_letter")
    if char != previous_char and (index + 1 >= len(text) or text[index + 1] != char):
        kinds.append("doubled_letter")
    if index + 1 < word_end:
        next_char = text[index + 1]
        if next_char != char:
            kinds.append("swapped_letters")
        if word_length >= 4 and char != previous_char and char != next_char:
            kinds.append("missing_letter")
    return kinds


def _left_kind_span(kind, index):
    """The (start, end) range of clean text that a mistake of this kind
    replaces. Only swapped letters cover two characters."""
    if kind == "swapped_letters":
        return index, index + 2
    return index, index + 1


def _left_is_sentence_end(text, index, protection_mask):
    """True for a . ! or ? that really ends a sentence: it follows a word of
    2+ letters that is not an abbreviation, and is followed by the end of the
    text, a line break, or a space and a capital letter."""
    if index < 2 or not text[index - 1].isalpha() or not text[index - 2].isalpha():
        return False
    word_start, word_end = _left_word_bounds(text, index - 1)
    if text[word_start:word_end].lower() in SMART_ABBREVIATIONS:
        return False
    if index + 1 < len(text) and text[index + 1] != "\n":
        if not (text[index + 1] == " " and index + 2 < len(text) and text[index + 2].isupper()):
            return False
    return _left_token_is_plain(text, index - 1, protection_mask)


def find_left_mistake_candidates(text, protection_mask, options, start_index,
                                 blocked_positions, pause_positions, rng):
    """Scans the text from start_index and returns the spots where a mistake
    could be left, as {group: [(kind, position), ...]}. `blocked_positions`
    are spots that must stay untouched; `pause_positions` are pause-marker
    positions, which a two-character mistake must not straddle."""
    pools = {group: [] for group in LEFT_MISTAKE_GROUPS}
    length = len(text)

    def span_is_free(start, end):
        for position in range(start, end):
            if protection_mask[position] or position in blocked_positions:
                return False
        for position in range(start + 1, end):
            if position in pause_positions:
                return False
        return True

    for index in range(start_index, length):
        char = text[index]
        if char.isalpha():
            if options["typos"]:
                kinds = _left_letter_kinds(text, index, protection_mask)
                if kinds:
                    kind = rng.choice(kinds)
                    span_start, span_end = _left_kind_span(kind, index)
                    if span_is_free(span_start, span_end):
                        pools["typos"].append((kind, index))
        elif char == " ":
            if (options["typos"]
                    and 0 < index < length - 1
                    and text[index - 1].isalpha()
                    and text[index + 1].isalpha()
                    and _left_token_is_plain(text, index - 1, protection_mask)
                    and _left_token_is_plain(text, index + 1, protection_mask)
                    and span_is_free(index, index + 1)):
                pools["typos"].append(("run_together", index))
        elif char == ",":
            if (options["commas"]
                    and 0 < index < length - 1
                    and text[index - 1].isalpha()
                    and text[index + 1] == " "
                    and _left_token_is_plain(text, index - 1, protection_mask)
                    and span_is_free(index, index + 1)):
                pools["commas"].append(("missing_comma", index))
        elif char in ".!?":
            if (options["punctuation"]
                    and _left_is_sentence_end(text, index, protection_mask)
                    and span_is_free(index, index + 1)):
                pools["punctuation"].append(("missing_period", index))
        elif char in "'\u2019":
            if (options["punctuation"]
                    and 0 < index < length - 1
                    and text[index - 1].isalpha()
                    and text[index + 1].isalpha()
                    and _left_token_is_plain(text, index - 1, protection_mask)
                    and span_is_free(index, index + 1)):
                pools["punctuation"].append(("missing_apostrophe", index))
    return pools


def _choose_spread_candidates(pools, count, start_index, text_length, rng):
    """Picks up to `count` candidates, spread out across the text and mixed
    between the groups (so commas and punctuation are not drowned out by the
    far more common typo spots). Starts with a wide minimum gap between
    mistakes and loosens it only if there are not enough spots."""
    span = max(text_length - start_index, 1)
    wide_gap = max(40, span // (count * 2))
    chosen = []
    chosen_positions = set()

    for min_gap in (wide_gap, wide_gap // 2, LEFT_MISTAKE_MIN_GAP):
        remaining = {}
        for group, items in pools.items():
            fresh = [item for item in items if item[1] not in chosen_positions]
            rng.shuffle(fresh)
            remaining[group] = fresh
        groups = [group for group, items in remaining.items() if items]

        while len(chosen) < count and groups:
            for group in list(groups):
                if len(chosen) >= count:
                    break
                items = remaining[group]
                picked = None
                while items:
                    candidate = items.pop()
                    if all(abs(candidate[1] - other) >= min_gap for other in chosen_positions):
                        picked = candidate
                        break
                if picked is not None:
                    chosen.append(picked)
                    chosen_positions.add(picked[1])
                if not items:
                    groups.remove(group)
        if len(chosen) >= count:
            break

    chosen.sort(key=lambda item: item[1])
    return chosen


def _build_left_mistake(text, kind, start, rng):
    """Returns (start, end, typed_text) for one chosen mistake: what gets
    typed in place of text[start:end]."""
    char = text[start]
    if kind == "wrong_letter":
        wrong = rng.choice(QWERTY_ADJACENCY[char.lower()])
        return start, start + 1, (wrong.upper() if char.isupper() else wrong)
    if kind == "doubled_letter":
        return start, start + 1, char + char
    if kind == "swapped_letters":
        return start, start + 2, text[start + 1] + char
    # missing_letter, run_together, missing_comma, missing_period,
    # missing_apostrophe: the character is simply not typed.
    return start, start + 1, ""


def _left_mistake_snippets(text, start, end, typed):
    """Returns (typed_snippet, correct_snippet) for the report. The whole
    word (or the two words, for a missing space) is shown between \u00ab \u00bb,
    with a little text on each side so it is easy to find with Ctrl+F."""
    chunk_start = start
    while chunk_start > 0 and not text[chunk_start - 1].isspace():
        chunk_start -= 1
    chunk_end = end
    while chunk_end < len(text) and not text[chunk_end].isspace():
        chunk_end += 1

    def show(piece):
        return piece.replace("\n", "\u21b5")

    head = show(text[max(0, chunk_start - LEFT_MISTAKE_CONTEXT):chunk_start])
    tail = show(text[chunk_end:chunk_end + LEFT_MISTAKE_CONTEXT])
    typed_chunk = text[chunk_start:start] + typed + text[end:chunk_end]
    correct_chunk = text[chunk_start:chunk_end]
    typed_snippet = f"{head}\u00ab{show(typed_chunk)}\u00bb{tail}"
    correct_snippet = f"{head}\u00ab{show(correct_chunk)}\u00bb{tail}"
    return typed_snippet, correct_snippet


def plan_left_mistakes(text, protection_mask, settings, start_index, pause_positions, blocked_positions):
    """Chooses the mistakes to leave in this file. Returns {start position:
    entry dict}. An entry has: kind, label, start, end, typed (what is typed
    instead of text[start:end]), correct, line, typed_snippet, and
    correct_snippet. Returns an empty dict when the mode is off. Fewer
    mistakes than asked for are planned when the text has too few safe spots."""
    options = get_left_mistake_options(settings)
    if options is None or not text:
        return {}
    count = int(settings.get("leave_mistake_count", LEFT_MISTAKE_DEFAULT_COUNT))
    count = max(1, min(count, LEFT_MISTAKE_MAX_COUNT))

    rng = random.Random()
    pools = find_left_mistake_candidates(
        text, protection_mask, options, start_index, blocked_positions, pause_positions, rng
    )
    chosen = _choose_spread_candidates(pools, count, start_index, len(text), rng)

    plan = {}
    for kind, position in chosen:
        start, end, typed = _build_left_mistake(text, kind, position, rng)
        typed_snippet, correct_snippet = _left_mistake_snippets(text, start, end, typed)
        plan[start] = {
            "kind": kind,
            "label": LEFT_MISTAKE_LABELS[kind],
            "start": start,
            "end": end,
            "typed": typed,
            "correct": text[start:end],
            "line": text.count("\n", 0, start) + 1,
            "typed_snippet": typed_snippet,
            "correct_snippet": correct_snippet,
        }
    return plan


# --------------------------------------------
# PARAPHRASE-THEN-CORRECT (Idea #10)
# --------------------------------------------
# A hand-written synonym dictionary for natural paragraph-ending
# rewording. Swaps common words with close alternatives so the app can
# simulate drafting a sentence, pausing to reconsider, deleting it,
# and retyping the true text.

PARAPHRASE_SYNONYMS = {
    # Common verbs
    "make": ["create", "produce", "build"],
    "makes": ["creates", "produces", "builds"],
    "made": ["created", "produced", "built"],
    "show": ["demonstrate", "display", "reveal"],
    "shows": ["demonstrates", "displays", "reveals"],
    "showed": ["demonstrated", "displayed", "revealed"],
    "use": ["utilize", "employ", "apply"],
    "uses": ["utilizes", "employs", "applies"],
    "used": ["utilized", "employed", "applied"],
    "start": ["begin", "commence", "initiate"],
    "starts": ["begins", "commences", "initiates"],
    "started": ["began", "commenced", "initiated"],
    "stop": ["halt", "end", "cease"],
    "stops": ["halts", "ends", "ceases"],
    "stopped": ["halted", "ended", "ceased"],
    "help": ["assist", "aid", "support"],
    "helps": ["assists", "aids", "supports"],
    "helped": ["assisted", "aided", "supported"],
    "need": ["require", "demand"],
    "needs": ["requires", "demands"],
    "needed": ["required", "demanded"],
    "think": ["believe", "consider", "feel"],
    "thinks": ["believes", "considers", "feels"],
    "thought": ["believed", "considered", "felt"],
    "find": ["discover", "locate", "identify"],
    "finds": ["discovers", "locates", "identifies"],
    "found": ["discovered", "located", "identified"],
    "give": ["provide", "offer", "grant"],
    "gives": ["provides", "offers", "grants"],
    "gave": ["provided", "offered", "granted"],
    "get": ["obtain", "receive", "acquire"],
    "gets": ["obtains", "receives", "acquires"],
    "got": ["obtained", "received", "acquired"],
    "tell": ["inform", "notify", "explain"],
    "tells": ["informs", "notifies", "explains"],
    "told": ["informed", "notified", "explained"],
    "try": ["attempt", "strive"],
    "tries": ["attempts", "strives"],
    "tried": ["attempted", "strived"],
    "check": ["verify", "inspect", "review"],
    "checks": ["verifies", "inspects", "reviews"],
    "checked": ["verified", "inspected", "reviewed"],
    "keep": ["maintain", "retain", "preserve"],
    "keeps": ["maintains", "retains", "preserves"],
    "kept": ["maintained", "retained", "preserved"],
    "change": ["alter", "modify", "adjust"],
    "changes": ["alters", "modifies", "adjusts"],
    "changed": ["altered", "modified", "adjusted"],
    # Adjectives & adverbs
    "quick": ["fast", "rapid", "swift"],
    "quickly": ["fast", "rapidly", "swiftly"],
    "slow": ["sluggish", "gradual", "unhurried"],
    "slowly": ["gradually", "unhurriedly"],
    "good": ["great", "fine", "solid"],
    "great": ["excellent", "wonderful", "fantastic"],
    "bad": ["poor", "flawed", "subpar"],
    "big": ["large", "substantial", "sizable"],
    "small": ["tiny", "minor", "slight"],
    "simple": ["straightforward", "basic", "easy"],
    "simply": ["just", "merely"],
    "hard": ["difficult", "tough", "challenging"],
    "easy": ["simple", "effortless", "straightforward"],
    "easily": ["smoothly", "readily"],
    "important": ["crucial", "vital", "essential"],
    "main": ["primary", "key", "central"],
    "clear": ["obvious", "evident", "apparent"],
    "clearly": ["plainly", "obviously", "evidently"],
    "correct": ["accurate", "right", "proper"],
    "wrong": ["incorrect", "inaccurate", "erroneous"],
    "certain": ["sure", "confident"],
    "certainly": ["surely", "definitely"],
    "maybe": ["perhaps", "possibly"],
    "often": ["frequently", "regularly"],
    "rarely": ["seldom", "hardly"],
    "always": ["consistently", "invariably"],
    "final": ["last", "ultimate", "concluding"],
    "finally": ["eventually", "at last", "ultimately"],
    # Transitions & nouns
    "however": ["nevertheless", "nonetheless", "yet"],
    "also": ["furthermore", "moreover"],
    "therefore": ["thus", "hence", "consequently"],
    "problem": ["issue", "trouble", "difficulty"],
    "problems": ["issues", "troubles", "difficulties"],
    "way": ["method", "manner", "approach"],
    "ways": ["methods", "approaches"],
    "idea": ["concept", "thought", "notion"],
    "ideas": ["concepts", "thoughts", "notions"],
    "task": ["job", "duty", "assignment"],
    "tasks": ["jobs", "duties", "assignments"],
    "result": ["outcome", "consequence"],
    "results": ["outcomes", "consequences"],
    "reason": ["cause", "rationale"],
    "reasons": ["causes", "rationales"],
    "part": ["portion", "section", "segment"],
    "parts": ["portions", "sections", "segments"],
}


def _match_case(original, replacement):
    """Preserves all-caps or title-case when substituting a word."""
    if original.isupper():
        return replacement.upper()
    if original and original[0].isupper():
        return replacement.capitalize()
    return replacement.lower()


def generate_paraphrase(sentence):
    """Substitutes available synonyms in `sentence` while keeping punctuation
    and casing intact. If too few words (fewer than 1 for short sentences,
    or fewer than 2 for sentences with 6+ words) have a synonym available,
    returns None to avoid typing broken fragments."""
    matches = list(re.finditer(r"\b[A-Za-z]+('?[A-Za-z]+)?\b", sentence))
    if not matches:
        return None

    candidates = []
    for m in matches:
        word = m.group(0)
        lower_word = word.lower()
        if lower_word in PARAPHRASE_SYNONYMS:
            candidates.append((m.start(), m.end(), word, PARAPHRASE_SYNONYMS[lower_word]))

    total_words = len(matches)
    if not candidates:
        return None
    if total_words >= 6 and len(candidates) < 2:
        return None

    swap_count = min(len(candidates), random.randint(1, min(3, len(candidates))))
    chosen_candidates = random.sample(candidates, swap_count)
    chosen_candidates.sort(key=lambda c: c[0])

    parts = []
    last_idx = 0
    for start, end, orig_word, syn_list in chosen_candidates:
        parts.append(sentence[last_idx:start])
        chosen_syn = random.choice(syn_list)
        parts.append(_match_case(orig_word, chosen_syn))
        last_idx = end
    parts.append(sentence[last_idx:])

    reworded = "".join(parts)
    if reworded == sentence:
        return None
    return reworded


def precompute_paragraph_paraphrases(text, protected_ranges):
    """Scans text for paragraphs (blocks ending in '\\n') and finds the last
    punctuated sentence in each paragraph. Pre-generates paraphrases for
    valid candidates that do not overlap protected ranges.

    Returns a dict mapping start_char_index -> (end_char_index, paraphrased_text, original_text).
    """
    paraphrase_map = {}
    if not text:
        return paraphrase_map

    # Paragraphs end at '\n'
    para_starts = [0]
    for m in re.finditer(r"\n", text):
        para_starts.append(m.end())

    for i in range(len(para_starts) - 1):
        p_start = para_starts[i]
        p_end = para_starts[i + 1] - 1  # exclude '\n'
        para_text = text[p_start:p_end]
        if not para_text.strip():
            continue

        term_matches = list(re.finditer(r'[.!?]+["\')\]]*', para_text))
        if not term_matches:
            continue

        last_term = term_matches[-1]
        sent_end_rel = last_term.end()

        if len(term_matches) > 1:
            prev_term = term_matches[-2]
            search_start = prev_term.end()
            while search_start < sent_end_rel and para_text[search_start].isspace():
                search_start += 1
            sent_start_rel = search_start
        else:
            search_start = 0
            while search_start < sent_end_rel and para_text[search_start].isspace():
                search_start += 1
            sent_start_rel = search_start

        if sent_start_rel >= sent_end_rel:
            continue

        abs_start = p_start + sent_start_rel
        abs_end = p_start + sent_end_rel
        sentence_str = text[abs_start:abs_end]

        overlaps_protected = any(
            max(abs_start, prot_start) < min(abs_end, prot_end)
            for prot_start, prot_end in protected_ranges
        )
        if overlaps_protected:
            continue

        paraphrased = generate_paraphrase(sentence_str)
        if paraphrased and paraphrased != sentence_str:
            paraphrase_map[abs_start] = (abs_end, paraphrased, sentence_str)

    return paraphrase_map


# --------------------------------------------
# SESSION LOGGING
# --------------------------------------------
def log_event(event, detail=""):
    """Append a timestamped event to the in-memory session log if
    logging is currently enabled."""
    if state.logging_enabled:
        state.log_entries.append((datetime.now().isoformat(timespec="seconds"), event, detail))


CSV_FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")


def make_csv_cell_safe(value):
    """Spreadsheet programs run any cell that starts with = + - or @ as a
    formula. Log details can hold pieces of typed text or a window title, so
    a leading apostrophe turns such a cell back into plain text."""
    text = str(value)
    if text.startswith(CSV_FORMULA_STARTERS):
        return "'" + text
    return text


def export_log(auto=False):
    """Write the current session log to a CSV file. When auto=True
    (called automatically at the end of a run) the file is saved
    next to the input file without prompting. When auto=False (the
    'Export Log' button) the user is asked where to save it."""
    if not state.log_entries:
        if not auto:
            messagebox.showinfo("No Log Data", "There is no session log data to export yet.")
        return None

    default_name = f"amanuensis_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    if auto:
        input_path = state.settings.get("input_path", "input.txt")
        input_dir = os.path.dirname(os.path.abspath(input_path)) or "."
        path = os.path.join(input_dir, default_name)
    else:
        path = filedialog.asksaveasfilename(
            title="Export Session Log",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return None

    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "event", "detail"])
            for log_row in state.log_entries:
                writer.writerow([make_csv_cell_safe(cell) for cell in log_row])
        if not auto:
            messagebox.showinfo("Log Exported", f"Session log saved to:\n{path}")
        return path
    except OSError as e:
        if not auto:
            messagebox.showerror("Export Failed", str(e))
        return None



def open_log_viewer():
    """Open a Toplevel window showing the in-memory session log (state.log_entries)
    in a ttk.Treeview table. Provides an 'Open Log File...' button to load a
    previously-exported CSV into the same table, replacing whatever was shown.
    Shows a 'No log data yet' message when the table is empty."""

    viewer = modern_toplevel(window, "Session Log Viewer", "700x420", (500, 300))

    # -- toolbar row --
    toolbar = modern_frame(viewer)
    toolbar.pack(fill="x", padx=12, pady=(10, 6))

    source_label_var = tk.StringVar(value="")
    modern_label(toolbar, textvariable=source_label_var, kind="muted").pack(side="left", fill="x", expand=True)

    def load_csv_file():
        """Ask the user for a CSV file and reload the treeview from it."""
        path = filedialog.askopenfilename(
            parent=viewer,
            title="Open Log File",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                rows = [(r.get("timestamp", ""), r.get("event", ""), r.get("detail", "")) for r in reader]
        except OSError as exc:
            err_str = str(exc)
            messagebox.showerror("Load Failed", f"Could not read file:\n{err_str}", parent=viewer)
            return
        except Exception as exc:
            err_str = str(exc)
            messagebox.showerror("Load Failed", f"Could not parse CSV:\n{err_str}", parent=viewer)
            return
        _populate_table(rows, source=os.path.basename(path))

    modern_button(toolbar, "Open Log File...", load_csv_file, kind="neutral").pack(side="right")

    # -- Treeview + scrollbars (the table itself is a native ttk.Treeview;
    # CustomTkinter has no table widget, so it sits inside a rounded frame) --
    tree_frame = modern_frame(viewer, bordered=True)
    tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    columns = ("timestamp", "event", "detail")
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
    tree.heading("timestamp", text="Timestamp")
    tree.heading("event", text="Event")
    tree.heading("detail", text="Detail")
    tree.column("timestamp", width=160, stretch=False, anchor="w")
    tree.column("event", width=180, stretch=False, anchor="w")
    tree.column("detail", width=300, stretch=True, anchor="w")

    vsb = modern_scrollbar(tree_frame, tree.yview)
    hsb = modern_scrollbar(tree_frame, tree.xview, horizontal=True)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    tree.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=(12, 0))
    vsb.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=(10, 0))
    hsb.grid(row=1, column=0, sticky="ew", padx=(10, 0), pady=(0, 8))
    tree_frame.rowconfigure(0, weight=1)
    tree_frame.columnconfigure(0, weight=1)

    # -- "no data" overlay label (shown when table is empty) --
    no_data_label = modern_label(
        tree_frame,
        "No log data yet.\nRun a session with logging enabled, or open a previously-exported CSV file.",
        kind="muted",
    )

    def _populate_table(rows, source="in-memory log"):
        """Clear the treeview and insert rows. Shows/hides the no-data overlay."""
        tree.delete(*tree.get_children())
        if rows:
            for ts, event, detail in rows:
                tree.insert("", tk.END, values=(ts, event, detail))
            no_data_label.place_forget()
            source_label_var.set(f"Source: {source}  \u2022  {len(rows)} row(s)")
        else:
            # Place the label centred over the treeview area
            no_data_label.place(relx=0.5, rely=0.5, anchor="center", in_=tree_frame)
            source_label_var.set("")

    # Populate immediately from in-memory log
    _populate_table(state.log_entries, source="last session (in-memory)")


# --------------------------------------------
# PREVIEW MODE (Preview Run button)
# --------------------------------------------
# When settings["preview_mode"] is True, type_text() runs completely
# unchanged -- same delays, pauses, mistakes, typos, and reworks --
# except its pyautogui call sites are routed through these three
# helpers instead of calling pyautogui directly. In preview mode they
# write into the Preview Run Toplevel's Text box (via
# run_on_main_thread, since type_text() always runs on the background
# typing thread) instead of sending real keystrokes.
preview_text_widget = None  # the active Preview Run Text widget, or None when no preview is open


def _preview_write(text):
    """Runs on the main thread via run_on_main_thread. Appends text
    to the preview Text box and scrolls to the end."""
    if preview_text_widget is None:
        return
    try:
        preview_text_widget.configure(state=tk.NORMAL)
        preview_text_widget.insert(tk.END, text)
        preview_text_widget.see(tk.END)
        preview_text_widget.configure(state=tk.DISABLED)
    except tk.TclError:
        pass


def _preview_backspace(count=1):
    """Runs on the main thread via run_on_main_thread. Deletes the
    last `count` characters from the preview Text box."""
    if preview_text_widget is None:
        return
    try:
        preview_text_widget.configure(state=tk.NORMAL)
        preview_text_widget.delete(f"end-{count + 1}c", "end-1c")
        preview_text_widget.see(tk.END)
        preview_text_widget.configure(state=tk.DISABLED)
    except tk.TclError:
        pass


def _preview_ctrl_backspace():
    """Runs on the main thread via run_on_main_thread. Deletes back to
    the start of the last word (mirroring a Ctrl+Backspace word-delete)
    from the preview Text box."""
    if preview_text_widget is None:
        return
    try:
        preview_text_widget.configure(state=tk.NORMAL)
        content = preview_text_widget.get("1.0", "end-1c")
        trimmed = content.rstrip()
        boundary = max(trimmed.rfind(" "), trimmed.rfind("\n"))
        cut_at = boundary + 1
        delete_count = len(content) - cut_at
        if delete_count > 0:
            preview_text_widget.delete(f"end-{delete_count + 1}c", "end-1c")
        preview_text_widget.see(tk.END)
        preview_text_widget.configure(state=tk.DISABLED)
    except tk.TclError:
        pass


def _send_char(settings, text):
    """Types `text` (a single character, or a short run of characters
    for a sentence-rework redo) as real keystrokes via keyboard.write(),
    or -- when settings['preview_mode'] is True -- into the Preview Run
    Text box instead. Always called from the background typing
    thread, so the preview write is routed through run_on_main_thread()."""
    if settings.get("preview_mode"):
        run_on_main_thread(lambda t=text: _preview_write(t))
    else:
        keyboard.write(text)


def _send_backspace(settings):
    """Presses Backspace once, or -- in preview mode -- removes the
    last character from the Preview Run Text box."""
    if settings.get("preview_mode"):
        run_on_main_thread(lambda: _preview_backspace(1))
    else:
        pyautogui.press("backspace")


def _send_ctrl_backspace(settings):
    """Sends Ctrl+Backspace (word delete), or -- in preview mode --
    removes the last word from the Preview Run Text box."""
    if settings.get("preview_mode"):
        run_on_main_thread(_preview_ctrl_backspace)
    else:
        pyautogui.hotkey("ctrl", "backspace")


def _send_format_toggle(settings, kind):
    """Presses Ctrl+B (bold) or Ctrl+I (italic), which toggles that style in
    Google Docs and Word. In preview mode nothing is pressed, because the
    shortcut would go to whatever window has focus instead of the preview."""
    if settings.get("preview_mode"):
        return
    pyautogui.hotkey("ctrl", FORMAT_SHORTCUT_LETTER[kind])


def _press_format_toggles(settings, kinds, active_formats):
    """Presses the shortcut for each style in `kinds`, with a short human
    pause before and after, and keeps the `active_formats` set (the styles
    that are on right now) up to date. Returns False if Stop was requested."""
    for kind in kinds:
        if stop_event.is_set():
            return False
        time.sleep(random.uniform(0.15, 0.35))  # reaching for the shortcut
        _send_format_toggle(settings, kind)
        if kind in active_formats:
            active_formats.discard(kind)
            log_event("format_off", kind)
        else:
            active_formats.add(kind)
            log_event("format_on", kind)
        time.sleep(random.uniform(0.05, 0.12))
    return not stop_event.is_set()


def _fail_and_stop(title, message):
    """Runs on the main thread (via run_on_main_thread). Shows an
    error dialog, then stops the session -- kept separate from
    stop_typing() so the background typing thread never calls
    Tkinter directly, only ever queues a request to do so."""
    messagebox.showerror(title, message)
    stop_typing()


def _info_and_stop(title, message):
    messagebox.showinfo(title, message)
    stop_typing()


# --------------------------------------------
# ACTIVITY WATCHER (Auto-Pause on Mouse Activity)
# --------------------------------------------
# When auto_pause_activity is enabled, this background watcher runs
# concurrently with typing. It samples cursor position every 100ms.
# If physical mouse movement exceeding the configured pixel sensitivity
# is detected, it triggers pause_event and notifies the user via the
# status bar. Resuming typing uses the normal resume control (button/hotkey).
def _activity_watcher(sensitivity):
    """Monitors mouse cursor position while typing is active.
    If the cursor moves more than `sensitivity` pixels from its resting position,
    triggers an automatic pause via the existing pause mechanism.
    All GUI calls are safely routed through run_on_main_thread()."""
    try:
        last_pos = pyautogui.position()
    except Exception:
        last_pos = (0, 0)

    while not stop_event.is_set() and state.is_running:
        time.sleep(0.1)
        if stop_event.is_set() or not state.is_running:
            break

        # During countdown, the user is switching to the target window -- don't pause,
        # just keep updating last_pos to the user's cursor position. The same goes for
        # a long session break (target time): the person is expected to be away or
        # busy, and may click back into the document when the break is ending.
        if (state.status.startswith("Starting in") or state.status == "Countdown..."
                or state.status.startswith("Session break")):
            try:
                last_pos = pyautogui.position()
            except Exception:
                pass
            continue

        # While already paused or focus lost, keep updating last_pos so resuming
        # doesn't immediately re-trigger a pause.
        if pause_event.is_set() or focus_lost_event.is_set():
            try:
                last_pos = pyautogui.position()
            except Exception:
                pass
            continue

        try:
            curr_pos = pyautogui.position()
        except Exception:
            continue

        dx = curr_pos[0] - last_pos[0]
        dy = curr_pos[1] - last_pos[1]
        dist = (dx * dx + dy * dy) ** 0.5
        if dist >= sensitivity:
            pause_event.set()
            state.status = "Activity detected \u2014 paused"
            log_event("activity_pause", f"mouse moved {dist:.1f}px")
            run_on_main_thread(lambda: btn_pause.configure(text="Resume Typing"))
            last_pos = curr_pos


# --------------------------------------------
# TARGET TIME TILL COMPLETION (Update #27)
# --------------------------------------------
# The person picks how long the whole run should take. Amanuensis first
# estimates how long the typing itself will take. The spare time that is left
# goes into long "session breaks", the way a person writes a long piece over
# several sittings instead of in one go. A longer target means longer breaks
# (an hour or more at a time for a target of several hours).
#
# How it works:
#   1. plan_session_breaks() runs once before typing starts. It decides how
#      many breaks there are, where they happen (paragraph starts first, then
#      sentence starts), and how big each break is compared to the others
#      (its "weight").
#   2. Right before each break, compute_session_break_seconds() works out its
#      real length from the time that is actually left. If typing ran slower or
#      faster than estimated, the later breaks absorb the difference, so the
#      run finishes close to the target.
#   3. Time the person spends paused is not counted against the target.
#
# Safety rules (do not weaken these):
#   * A break never happens inside [[protected text]].
#   * Amanuensis never types faster than the speed settings. A target that is
#     shorter than the typing time simply cannot be reached.
#   * Preview runs ignore the target, so a preview never waits for hours.
TARGET_TIME_MIN_SECONDS = 60.0
TARGET_TIME_MAX_HOURS = 48
SESSION_BREAK_MIN_BUDGET_SECONDS = 120.0  # spare time needed before any break is planned
SESSION_BREAK_MIN_SECONDS = 30.0  # a break that would be shorter than this is skipped
SESSION_BREAK_MAX_SECONDS = 6 * 3600.0  # no single break is longer than this
SESSION_BREAK_MAX_COUNT = 60
SESSION_BREAK_EDGE_FRACTION = 0.03  # keep breaks away from the very start and very end
SESSION_BREAK_RESUME_SECONDS = 10  # warning countdown after a break when no window is locked
SESSION_PACE_MIN_ESTIMATE_SECONDS = 60.0  # too little typing so far to judge the pace


def format_duration_text(seconds):
    """Turns seconds into short text such as '45s', '12m 05s', or '7h 30m'."""
    total = max(int(seconds + 0.5), 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def estimate_active_seconds(text, settings, protection_mask, pause_map, start, end):
    """Rough typing time for text[start:end], using the same estimate as the
    pre-start time estimate. Inline {{pause:N}} markers inside the range count
    too. Session breaks are NOT included, because they are what is being planned."""
    if end <= start:
        return 0.0
    segment = text[start:end]
    segment_mask = protection_mask[start:end]
    marker_seconds = sum(secs for pos, secs in pause_map.items() if start <= pos < end)
    _words, _chars, seconds = estimate_typing_time(segment, settings, segment_mask, marker_seconds)
    return seconds


def plan_session_break_count(spare_seconds):
    """How many long breaks to plan for this much spare time. The count grows
    with the square root of the spare time, so a longer target gets more breaks
    and each break is longer too (about 5 breaks for 1 hour of spare time and
    about 11 for 6 hours)."""
    if spare_seconds < SESSION_BREAK_MIN_BUDGET_SECONDS:
        return 0
    minutes = spare_seconds / 60.0
    count = int(round(0.6 * (minutes ** 0.5)))
    return min(max(count, 1), SESSION_BREAK_MAX_COUNT)


def _session_break_whitespace_before(text, index):
    """Looks at the run of whitespace just before `index`. Returns how many
    newlines it holds and the character right before the run."""
    newline_count = 0
    cursor = index
    while cursor > 0 and text[cursor - 1].isspace():
        cursor -= 1
        if text[cursor] == "\n":
            newline_count += 1
    previous_char = text[cursor - 1] if cursor > 0 else ""
    return newline_count, previous_char


def _session_break_is_real_sentence_end(punct, previous_token):
    """False when a period only ends an abbreviation ("Dr.", "e.g."), a single
    initial, a list number, or a web address, so a break never lands there."""
    if not punct.endswith("."):
        return True  # ! and ? always end a sentence
    token = previous_token.strip(SMART_TRAILING_PUNCT + "([\"'").lower()
    if not token:
        return False
    if token in SMART_ABBREVIATIONS or token in SMART_ROMAN_NUMERALS:
        return False
    if len(token) == 1 and token.isalpha():
        return False
    if "." in token or _smart_looks_technical(token):
        return False
    return True


def find_session_break_candidates(text, protection_mask, start_index):
    """Finds the places where a person would naturally stop for a while.
    Returns (paragraph_starts, sentence_starts). Each number is the index of the
    first character of the next paragraph or sentence, and the break happens
    just before that character is typed. Spots inside [[protected text]], near
    the very start or end, or in the middle of a hard-wrapped line are skipped."""
    paragraph_starts = []
    sentence_starts = []
    text_length = len(text)
    edge = int((text_length - start_index) * SESSION_BREAK_EDGE_FRACTION)
    first_allowed = start_index + edge
    last_allowed = text_length - 1 - edge
    for index, punct, previous_token in _smart_sentence_starts(text):
        if index <= first_allowed or index > last_allowed or index >= text_length:
            continue
        if protection_mask[index - 1] and protection_mask[index]:
            continue  # inside protected text
        newline_count, previous_char = _session_break_whitespace_before(text, index)
        if newline_count > 0:
            # A new paragraph: a blank line, or a line that ended a sentence.
            # A single newline after a word is a hard-wrapped line, so skip it.
            if newline_count >= 2 or previous_char in ".!?:\"'\u201d\u2019)":
                paragraph_starts.append(index)
        elif punct and _session_break_is_real_sentence_end(punct, previous_token):
            sentence_starts.append(index)
    return paragraph_starts, sentence_starts


def _nearest_unused_position(candidates, ideal, used, max_distance):
    """The candidate closest to `ideal` that is not already used, or None if
    there is none (or none within max_distance, when a limit is given)."""
    best = None
    best_distance = None
    for candidate in candidates:
        if candidate in used:
            continue
        distance = abs(candidate - ideal)
        if max_distance is not None and distance > max_distance:
            continue
        if best is None or distance < best_distance:
            best = candidate
            best_distance = distance
    return best


def choose_session_break_positions(paragraph_starts, sentence_starts, count, start_index, text_length, rng=random):
    """Picks where the breaks go. They are spread across the text with some
    random jitter, snapped to a paragraph start when one is close and to a
    sentence start otherwise. Fewer breaks than `count` are returned when the
    text has too few good spots."""
    span = text_length - start_index
    pool = sorted(set(paragraph_starts) | set(sentence_starts))
    count = min(count, len(pool))
    if count <= 0 or span <= 0:
        return []
    gap = span / (count + 1)
    used = set()
    for number in range(1, count + 1):
        ideal = start_index + gap * number + rng.uniform(-0.3, 0.3) * gap
        pick = _nearest_unused_position(paragraph_starts, ideal, used, 0.45 * gap)
        if pick is None:
            pick = _nearest_unused_position(pool, ideal, used, None)
        # Never put two breaks right next to each other.
        if pick is not None and all(abs(pick - other) >= 0.25 * gap for other in used):
            used.add(pick)
    return sorted(used)


def make_session_break_weights(count, rng=random):
    """Relative size of each break. Most are ordinary and some are much
    longer, like a meal, a walk, or sleeping on it. With four or more breaks
    at least one is a long one."""
    weights = []
    any_long = False
    for _ in range(count):
        weight = max(rng.lognormvariate(0.0, 0.45), 0.3)
        if rng.random() < 0.25:
            weight *= 2.5
            any_long = True
        weights.append(weight)
    if count >= 4 and not any_long:
        weights[rng.randrange(count)] *= 2.5
    return weights


def plan_session_breaks(text, protection_mask, pause_map, settings, start_index, target_seconds, rng=random):
    """Plans the long session breaks for one file. Returns (breaks, spare_seconds,
    typing_seconds). `breaks` is a list of {"position", "weight"} dicts in text
    order. It is empty when the target leaves no spare time (or the text has no
    good place to stop)."""
    typing_seconds = estimate_active_seconds(
        text, settings, protection_mask, pause_map, start_index, len(text)
    )
    spare_seconds = target_seconds - typing_seconds
    count = plan_session_break_count(spare_seconds)
    if count == 0:
        return [], spare_seconds, typing_seconds
    paragraph_starts, sentence_starts = find_session_break_candidates(text, protection_mask, start_index)
    positions = choose_session_break_positions(
        paragraph_starts, sentence_starts, count, start_index, len(text), rng
    )
    weights = make_session_break_weights(len(positions), rng)
    breaks = [{"position": position, "weight": weight} for position, weight in zip(positions, weights)]
    return breaks, spare_seconds, typing_seconds


def compute_session_break_seconds(text, settings, protection_mask, pause_map, start_index, position,
                                  target_seconds, elapsed_active, break_seconds_so_far,
                                  this_weight, total_weight):
    """Works out how long the break at `position` should last, from the time
    that is actually left. `elapsed_active` is the time spent typing so far
    (breaks and the person's own pauses left out). The pace so far is compared
    with the estimate, so a slower or faster run changes the later breaks.
    Returns 0.0 when the break should be skipped (running behind the target, or
    the break would be too short to be worth it)."""
    estimate_done = estimate_active_seconds(
        text, settings, protection_mask, pause_map, start_index, position
    )
    estimate_left = estimate_active_seconds(
        text, settings, protection_mask, pause_map, position, len(text)
    )
    if estimate_done >= SESSION_PACE_MIN_ESTIMATE_SECONDS:
        pace = min(max(elapsed_active / estimate_done, 0.5), 3.0)
    else:
        pace = 1.0
    predicted_left = estimate_left * pace
    spare_seconds = target_seconds - elapsed_active - break_seconds_so_far - predicted_left
    if spare_seconds <= 0 or total_weight <= 0:
        return 0.0
    seconds = spare_seconds * this_weight / total_weight
    if seconds < SESSION_BREAK_MIN_SECONDS:
        return 0.0
    return min(seconds, SESSION_BREAK_MAX_SECONDS)


# --------------------------------------------
# DIFFICULTY-AWARE THINKING PAUSES (Update #33)
# --------------------------------------------
# Real people slow down on hard words and on long or packed sentences. Before
# typing starts, the text is scanned once. A word earns "difficulty points"
# for being long, having many syllables, holding digits, being an ALL-CAPS
# acronym, or using rare letters. Common everyday words never count. A
# sentence is "hard" when it is very long, holds several hard words, or is
# packed with commas. The plan only says WHERE a pause may happen; the typing
# loop draws the chance and the length when it gets there.
DIFFICULTY_WORD_MIN_SCORE = 3       # points a word needs to count as hard
DIFFICULTY_WORD_CHANCE = 0.7        # share of hard words that get a pause
DIFFICULTY_WORD_GAP_WORDS = 4       # at least this many words between word pauses
DIFFICULTY_WORD_SECONDS = (0.5, 1.5)
DIFFICULTY_SENTENCE_CHANCE = 0.8    # share of hard sentences that get a pause
DIFFICULTY_SENTENCE_SECONDS = (1.5, 3.5)
DIFFICULTY_LONG_SENTENCE_WORDS = 30
DIFFICULTY_DENSE_SENTENCE_HARD_WORDS = 3
DIFFICULTY_COMMA_HEAVY_COUNT = 4
DIFFICULTY_STRENGTH_MAX = 300.0

DIFFICULTY_WORD_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\u2019\-]*")
DIFFICULTY_VOWEL_GROUP_PATTERN = re.compile(r"[aeiouy]+")
DIFFICULTY_SENTENCE_CLOSERS = "\"')]\u201d\u2019"

# Long words that are still easy to think of and type. They never count as hard.
DIFFICULTY_COMMON_WORDS = frozenset("""
about above across actually after again against almost already although always
among another anything around because become before behind being believe
between beyond building business change children community company consider
continue country different difficult discuss during education effect either
enough environment especially everything example experience family following
government great history however important include information interest
itself knowledge language learning little looking material meeting message
morning nothing number office opportunity others people percent perhaps person
personal place political possible present president problem program public
question reason receive remember research response result school second
service should something sometimes started state student study support system
taking thing think those though thought through together tomorrow towards
understand university usually various water without wonder working world
would write written yesterday
""".split())


def count_syllables(word):
    """Rough syllable count: one per group of vowels, ignoring a silent final e."""
    lowered = re.sub(r"[^a-z]", "", word.lower())
    if not lowered:
        return 0
    count = len(DIFFICULTY_VOWEL_GROUP_PATTERN.findall(lowered))
    if lowered.endswith("e") and not lowered.endswith(("le", "ee")) and count > 1:
        count -= 1
    return max(count, 1)


def score_word_difficulty(word):
    """Gives a word points for how hard it is to think of and type. Common
    everyday words and very short words score 0."""
    core = word.strip("-'\u2019")
    letters = re.sub(r"[^A-Za-z]", "", core)
    if len(core) < 4:
        return 0
    if core.lower() in DIFFICULTY_COMMON_WORDS:
        return 0
    points = 0
    if len(letters) >= 12:
        points += 2
    elif len(letters) >= 8:
        points += 1
    syllables = count_syllables(letters)
    if syllables >= 5:
        points += 3
    elif syllables >= 4:
        points += 2
    elif syllables >= 3:
        points += 1
    if any(ch.isdigit() for ch in core):
        points += 2
    if len(letters) >= 3 and letters.isupper():
        points += 2  # an acronym such as NASA or HIPAA
    if any(ch in "qxzjQXZJ" for ch in letters):
        points += 1
    return points


def find_sentence_spans(text):
    """Splits text into (start, end) sentence spans. A span starts at the first
    character after a sentence end (or a paragraph break) and stops at the next
    sentence end. A period after an abbreviation like "Dr." does not end one."""
    spans = []
    length = len(text)
    start = 0
    cursor = 0
    while cursor < length:
        char = text[cursor]
        is_end = False
        if char == "\n":
            is_end = True
        elif char in ".!?":
            run_end = cursor
            while run_end + 1 < length and text[run_end + 1] in ".!?":
                run_end += 1
            after = run_end + 1
            while after < length and text[after] in DIFFICULTY_SENTENCE_CLOSERS:
                after += 1
            if after >= length or text[after].isspace():
                token_start = cursor
                while token_start > start and not text[token_start - 1].isspace():
                    token_start -= 1
                if _session_break_is_real_sentence_end(text[cursor:run_end + 1], text[token_start:cursor]):
                    is_end = True
                    cursor = after - 1
        if is_end:
            if text[start:cursor + 1].strip():
                spans.append((start, cursor + 1))
            start = cursor + 1
        cursor += 1
    if text[start:].strip():
        spans.append((start, length))
    return spans


def plan_difficulty_pauses(text, protection_mask, start_index=0):
    """Finds the spots where a hard word or a hard sentence earns a thinking
    pause. Returns {index of the first character: (kind, weight)} where kind is
    "word" or "sentence" and weight (1.0 and up) says how hard it is. Only text
    from start_index on is planned. Protected text is never touched, and a
    sentence pause wins over a word pause at the same spot."""
    plan = {}
    for span_start, span_end in find_sentence_spans(text):
        if span_end <= start_index:
            continue
        words = []
        for match in DIFFICULTY_WORD_PATTERN.finditer(text, span_start, span_end):
            if any(protection_mask[match.start():match.end()]):
                words.append((match.start(), 0))
            else:
                words.append((match.start(), score_word_difficulty(match.group())))
        if not words:
            continue

        hard_count = sum(1 for _position, score in words if score >= DIFFICULTY_WORD_MIN_SCORE)
        segment = text[span_start:span_end]
        reasons = 0
        if len(words) >= DIFFICULTY_LONG_SENTENCE_WORDS:
            reasons += 1
        if hard_count >= DIFFICULTY_DENSE_SENTENCE_HARD_WORDS:
            reasons += 1
        if sum(segment.count(mark) for mark in ",;:") >= DIFFICULTY_COMMA_HEAVY_COUNT:
            reasons += 1
        first_position = words[0][0]
        if reasons and first_position >= start_index and not protection_mask[first_position]:
            plan[first_position] = ("sentence", 1.0 + 0.25 * (reasons - 1))

        # A word pause needs a few words of room after the last pause, and a
        # sentence pause counts as the last pause for the sentence's first word.
        words_since_pause = 0 if first_position in plan else DIFFICULTY_WORD_GAP_WORDS
        for position, score in words:
            words_since_pause += 1
            if (position < start_index or position in plan
                    or score < DIFFICULTY_WORD_MIN_SCORE
                    or words_since_pause <= DIFFICULTY_WORD_GAP_WORDS):
                continue
            plan[position] = ("word", min(score / 3.0, 2.0))
            words_since_pause = 0
    return plan


def draw_difficulty_pause_seconds(kind, weight, strength_percent):
    """Decides whether this planned spot really pauses and for how long.
    Returns 0.0 for no pause. The length scales with how hard the spot is and
    with the Pause Strength setting."""
    if kind == "sentence":
        chance, (low, high) = DIFFICULTY_SENTENCE_CHANCE, DIFFICULTY_SENTENCE_SECONDS
    else:
        chance, (low, high) = DIFFICULTY_WORD_CHANCE, DIFFICULTY_WORD_SECONDS
    if strength_percent <= 0 or random.random() >= chance:
        return 0.0
    return random.uniform(low, high) * weight * (strength_percent / 100.0)


def estimate_difficulty_pause_seconds(text, protection_mask, settings):
    """Average extra time the thinking pauses add to `text`. Uses the same
    plan, chances, and ranges the typing loop draws from."""
    if not settings.get("enable_difficulty_pauses", True):
        return 0.0
    strength = settings.get("difficulty_pause_strength", 100.0) / 100.0
    if strength <= 0:
        return 0.0
    mask = protection_mask if protection_mask is not None else [False] * len(text)
    total = 0.0
    for kind, weight in plan_difficulty_pauses(text, mask).values():
        if kind == "sentence":
            average = sum(DIFFICULTY_SENTENCE_SECONDS) / 2.0
            total += DIFFICULTY_SENTENCE_CHANCE * average * weight * strength
        else:
            average = sum(DIFFICULTY_WORD_SECONDS) / 2.0
            total += DIFFICULTY_WORD_CHANCE * average * weight * strength
    return total


# --------------------------------------------
# FATIGUE DRIFT
# --------------------------------------------
def get_fatigue_multiplier(chars_since_break, fatigue_strength, ramp_chars=2000):
    """Calculates a delay multiplier representing human fatigue.

    As characters are typed without a break, the multiplier gently ramps
    from 1.0 (normal delay) up to (1.0 + fatigue_strength / 100.0).
    After a research break, chars_since_break drops back toward normal to
    simulate recovery.

    Args:
        chars_since_break (int or float): Number of characters typed since
            session start or last break recovery.
        fatigue_strength (float): Maximum slowdown percentage (e.g. 15 for 15%).
            Safe range is clamped between 0 and 40.
        ramp_chars (int): Character count over which maximum fatigue develops.
            Defaults to 2000 characters.

    Returns:
        float: Multiplier >= 1.0 to scale per-key delays. Never negative or extreme.
    """
    try:
        strength = max(0.0, min(float(fatigue_strength), 40.0))
    except (ValueError, TypeError):
        strength = 0.0

    try:
        chars = max(0.0, float(chars_since_break))
    except (ValueError, TypeError):
        chars = 0.0

    ramp = max(1.0, float(ramp_chars))
    progress = min(chars / ramp, 1.0)
    multiplier = 1.0 + (strength / 100.0) * progress
    # Clamp safely to ensure delays are never negative or extreme
    return max(1.0, min(multiplier, 1.0 + (strength / 100.0)))


# --------------------------------------------
# FLOW AND HESITATION STATES (Update #31)
# --------------------------------------------
# Fatigue drift only slows the typist down over time. Real typing also has
# moods: a stretch where the words are coming easily (flow) and a stretch of
# second-guessing (hesitation). The typist moves between three states:
#   normal -> flow or hesitant -> normal -> ...
# Each state lasts a random number of characters. The speed changes ease in
# over a few characters so it never jumps. Only speed changes here.
MOOD_NORMAL = "normal"
MOOD_FLOW = "flow"
MOOD_HESITANT = "hesitant"
MOOD_LENGTH_CHARS = {
    MOOD_NORMAL: (100, 300),
    MOOD_FLOW: (80, 270),
    MOOD_HESITANT: (40, 150),
}
MOOD_FLOW_CHANCE = 0.55         # chance that the next mood after normal is flow
MOOD_FLOW_SPEEDUP = 0.15        # at 100% strength, flow is 15% quicker
MOOD_HESITANT_SLOWDOWN = 0.25   # at 100% strength, hesitation is 25% slower
MOOD_EASE_CHARS = 12            # characters it takes to ease into a new speed
MOOD_STRENGTH_MAX = 200.0


def clamp_mood_strength(strength_percent):
    """Returns the mood strength as a float between 0 and MOOD_STRENGTH_MAX."""
    try:
        return max(0.0, min(float(strength_percent), MOOD_STRENGTH_MAX))
    except (ValueError, TypeError):
        return 0.0


def get_mood_target_multiplier(mood, strength_percent):
    """Returns the delay multiplier a mood is heading toward (never below 0.5)."""
    strength = clamp_mood_strength(strength_percent) / 100.0
    if mood == MOOD_FLOW:
        return max(0.5, 1.0 - MOOD_FLOW_SPEEDUP * strength)
    if mood == MOOD_HESITANT:
        return 1.0 + MOOD_HESITANT_SLOWDOWN * strength
    return 1.0


def estimate_mood_multiplier(strength_percent):
    """Average delay multiplier over a long run, for the pre-start estimate.

    One cycle is a normal stretch followed by either a flow stretch or a
    hesitant stretch, each weighted by its average length in characters.
    """
    def average_length(mood):
        low, high = MOOD_LENGTH_CHARS[mood]
        return (low + high) / 2.0

    flow_chars = MOOD_FLOW_CHANCE * average_length(MOOD_FLOW)
    hesitant_chars = (1.0 - MOOD_FLOW_CHANCE) * average_length(MOOD_HESITANT)
    normal_chars = average_length(MOOD_NORMAL)
    total_chars = normal_chars + flow_chars + hesitant_chars
    weighted = (
        normal_chars * get_mood_target_multiplier(MOOD_NORMAL, strength_percent)
        + flow_chars * get_mood_target_multiplier(MOOD_FLOW, strength_percent)
        + hesitant_chars * get_mood_target_multiplier(MOOD_HESITANT, strength_percent)
    )
    return weighted / total_chars


class TypingMood:
    """Tracks the typist's current mood and gives a delay multiplier per key."""

    def __init__(self, strength_percent):
        self.strength = clamp_mood_strength(strength_percent)
        self.mood = MOOD_NORMAL
        self.multiplier = 1.0
        self.chars_left = self._draw_length(MOOD_NORMAL)

    @staticmethod
    def _draw_length(mood):
        low, high = MOOD_LENGTH_CHARS[mood]
        return random.randint(low, high)

    def reset(self):
        """A long rest puts the typist back in the normal state right away."""
        self.mood = MOOD_NORMAL
        self.multiplier = 1.0
        self.chars_left = self._draw_length(MOOD_NORMAL)

    def step(self):
        """Call once per typed character.

        Returns (multiplier, new_mood). new_mood is the name of the mood that
        just started, or None when the mood did not change on this character.
        """
        new_mood = None
        self.chars_left -= 1
        if self.chars_left <= 0:
            if self.mood == MOOD_NORMAL:
                self.mood = MOOD_FLOW if random.random() < MOOD_FLOW_CHANCE else MOOD_HESITANT
            else:
                self.mood = MOOD_NORMAL
            self.chars_left = self._draw_length(self.mood)
            new_mood = self.mood
        target = get_mood_target_multiplier(self.mood, self.strength)
        self.multiplier += (target - self.multiplier) / MOOD_EASE_CHARS
        return self.multiplier, new_mood


# --------------------------------------------
# TYPO SIMULATION HELPERS
# --------------------------------------------
def _simulate_adjacent_key_typo(settings, char, text, idx, protection_mask, use_extra_typos):
    """Simulates an adjacent-key typo on a QWERTY keyboard.
    When use_extra_typos is False, preserves the original quick wrong-and-backspace behavior.
    When use_extra_typos is True, types 1-3 lookahead characters before pausing and backspacing."""
    wrong_char = random.choice(QWERTY_ADJACENCY[char.lower()])
    if char.isupper():
        wrong_char = wrong_char.upper()

    if not use_extra_typos:
        _send_char(settings, wrong_char)
        time.sleep(random.uniform(0.08, 0.25))
        _send_backspace(settings)
        time.sleep(random.uniform(0.05, 0.15))
        log_event("typo", f"typed '{wrong_char}' instead of '{char}'")
        return

    # Extra typos enabled: delayed notice with 1-3 lookahead characters
    lookahead = []
    max_k = min(3, len(text) - (idx + 1))
    for offset in range(1, max_k + 1):
        pos = idx + offset
        if protection_mask[pos] or text[pos] == "\n":
            break
        lookahead.append(text[pos])

    k = random.randint(1, len(lookahead)) if lookahead else 0
    lookahead_str = "".join(lookahead[:k])

    _send_char(settings, wrong_char)
    time.sleep(random.uniform(0.06, 0.15))
    for lc in lookahead_str:
        if stop_event.is_set():
            return
        _send_char(settings, lc)
        time.sleep(random.uniform(0.06, 0.15))

    # Delayed notice hesitation
    time.sleep(random.uniform(0.25, 0.55))

    # Backspace lookahead + wrong_char
    for _ in range(len(lookahead_str) + 1):
        if stop_event.is_set():
            return
        _send_backspace(settings)
        time.sleep(random.uniform(0.05, 0.12))

    time.sleep(random.uniform(0.08, 0.18))
    log_event("typo", f"adjacent-key: typed '{wrong_char}{lookahead_str}', backspaced to retype '{char}'")


def _simulate_swapped_letter_typo(settings, text, idx, protection_mask):
    """Simulates swapped adjacent letters inside a word (e.g. 'teh' for 'the').
    Types c2 then c1, followed by 1-3 lookahead characters, pauses, then backspaces
    all of them so the caller loop can retype the characters in correct order."""
    c1 = text[idx]
    c2 = text[idx + 1]

    lookahead = []
    max_k = min(3, len(text) - (idx + 2))
    for offset in range(1, max_k + 1):
        pos = idx + 1 + offset
        if protection_mask[pos] or text[pos] == "\n":
            break
        lookahead.append(text[pos])

    k = random.randint(1, len(lookahead)) if lookahead else 0
    lookahead_str = "".join(lookahead[:k])

    _send_char(settings, c2)
    time.sleep(random.uniform(0.06, 0.15))
    _send_char(settings, c1)
    time.sleep(random.uniform(0.06, 0.15))
    for lc in lookahead_str:
        if stop_event.is_set():
            return
        _send_char(settings, lc)
        time.sleep(random.uniform(0.06, 0.15))

    # Delayed notice hesitation
    time.sleep(random.uniform(0.25, 0.55))

    # Backspace lookahead + both swapped letters
    for _ in range(len(lookahead_str) + 2):
        if stop_event.is_set():
            return
        _send_backspace(settings)
        time.sleep(random.uniform(0.05, 0.12))

    time.sleep(random.uniform(0.08, 0.18))
    log_event("typo", f"swapped letters: typed '{c2}{c1}{lookahead_str}', backspaced to retype '{c1}{c2}'")


def _simulate_doubled_letter_typo(settings, text, idx, protection_mask):
    """Simulates an accidental doubled letter ('ttthe' style repeat).
    Types the letter twice, follows with 0-3 lookahead characters, pauses,
    and backspaces the lookahead characters plus the single extra duplicate letter.
    Leaves the original intended letter on screen and returns True so caller
    knows not to type char a second time."""
    char = text[idx]

    lookahead = []
    max_k = min(3, len(text) - (idx + 1))
    for offset in range(1, max_k + 1):
        pos = idx + offset
        if protection_mask[pos] or text[pos] == "\n":
            break
        lookahead.append(text[pos])

    k = random.randint(0, len(lookahead)) if lookahead else 0
    lookahead_str = "".join(lookahead[:k])

    _send_char(settings, char)
    time.sleep(random.uniform(0.04, 0.10))
    _send_char(settings, char)
    time.sleep(random.uniform(0.06, 0.15))
    for lc in lookahead_str:
        if stop_event.is_set():
            return False
        _send_char(settings, lc)
        time.sleep(random.uniform(0.06, 0.15))

    # Delayed notice hesitation
    time.sleep(random.uniform(0.20, 0.50))

    # Backspace lookahead + single extra duplicate letter
    for _ in range(len(lookahead_str) + 1):
        if stop_event.is_set():
            return False
        _send_backspace(settings)
        time.sleep(random.uniform(0.05, 0.12))

    time.sleep(random.uniform(0.08, 0.18))
    log_event("typo", f"doubled letter: typed '{char}{char}{lookahead_str}', backspaced duplicate")
    return True


def _simulate_missed_space_typo(settings, text, idx, protection_mask):
    """Simulates a missed space where two words run together (e.g. 'forthe').
    Does not type the space; instead types 1-3 characters of the next word,
    pauses, and backspaces them so the caller loop can type the space normally."""
    lookahead = []
    max_k = min(3, len(text) - (idx + 1))
    for offset in range(1, max_k + 1):
        pos = idx + offset
        if protection_mask[pos] or text[pos] in ("\n", " "):
            break
        lookahead.append(text[pos])

    if not lookahead:
        return

    k = random.randint(1, len(lookahead))
    lookahead_str = "".join(lookahead[:k])

    for lc in lookahead_str:
        if stop_event.is_set():
            return
        _send_char(settings, lc)
        time.sleep(random.uniform(0.06, 0.15))

    # Delayed notice hesitation
    time.sleep(random.uniform(0.25, 0.55))

    # Backspace all run-together characters
    for _ in range(len(lookahead_str)):
        if stop_event.is_set():
            return
        _send_backspace(settings)
        time.sleep(random.uniform(0.05, 0.12))

    time.sleep(random.uniform(0.08, 0.18))
    log_event("typo", f"missed space: ran together into '{lookahead_str}', backspaced to insert space")


def _act_out_smart_fix(settings, kind, wrong_text):
    """Acts out one smart fix the way a real person would: types the slip
    (wrong_text), pauses as if noticing it, then backspaces it. The caller
    then types the correct text, so the screen ends up exactly as it would
    have without this, and the final text never changes. Once the slip has
    been typed, the backspaces always finish even if Stop is pressed, so no
    stray characters are ever left behind. Returns False if Stop was
    requested (the caller should return right away)."""
    state.status = "Noticed a slip..."
    typed_count = 0
    for slip_char in wrong_text:
        if stop_event.is_set():
            break
        _send_char(settings, slip_char)
        typed_count += 1
        time.sleep(random.uniform(0.06, 0.15))

    if typed_count and not stop_event.is_set():
        time.sleep(random.uniform(0.25, 0.55))  # the "wait, that's wrong" moment

    for _ in range(typed_count):
        _send_backspace(settings)
        time.sleep(random.uniform(0.05, 0.12))

    if typed_count and not stop_event.is_set():
        time.sleep(random.uniform(0.08, 0.18))
        log_event("smart_fix_noticed", kind)
    return not stop_event.is_set()


def _type_left_mistake(settings, entry, input_path):
    """Types one purposely left mistake (Update #24) in place of the correct
    text, and records it for the report window. Nothing is ever backspaced:
    the mistake is meant to stay on screen for the person to find."""
    typed = entry["typed"]
    for typed_char in typed:
        _send_char(settings, typed_char)
        if len(typed) > 1:
            time.sleep(random.uniform(0.04, 0.10))

    record = dict(entry)
    record["file"] = os.path.basename(input_path)
    state.left_mistakes.append(record)
    log_event(
        "mistake_left",
        f"line {entry['line']}: {entry['label']}: typed {entry['typed_snippet']} "
        f"(should be {entry['correct_snippet']})",
    )


def _note_blocked_time(seconds):
    """Records time spent paused (by the person, by activity auto-pause, or
    because focus was lost). That time is left out of the live WPM and ETA, is
    not counted against a target time, and moves the target finish time later
    by the same amount (Update #27)."""
    state.paused_duration += seconds
    state.blocked_seconds += seconds
    if state.target_end_time > 0:
        state.target_end_time += seconds


def _wait_for_session_break(seconds, break_number, break_total):
    """Waits `seconds` for a long session break (target time), in small steps
    so Stop and Pause still work. While paused, the countdown freezes and picks
    up where it left off. The status line always starts with "Session break":
    the activity auto-pause watcher relies on that to ignore mouse movement,
    because the person is expected to be away from the keyboard during a break.
    Returns True when the wait finished, or False if Stop was requested."""
    remaining = float(seconds)
    last_tick = time.time()
    while remaining > 0:
        if stop_event.is_set():
            return False

        if pause_event.is_set():
            block_start = time.time()
            while pause_event.is_set():
                if stop_event.is_set():
                    return False
                if not state.status.startswith("Activity detected"):
                    state.status = "Paused"
                time.sleep(0.2)
            _note_blocked_time(time.time() - block_start)
            last_tick = time.time()
            continue

        state.status = (
            f"Session break {break_number} of {break_total} "
            f"({format_duration_text(remaining)} left)..."
        )
        time.sleep(min(0.25, remaining))
        now = time.time()
        elapsed = now - last_tick
        last_tick = now
        remaining -= elapsed
        state.paused_duration += elapsed
    return True


def _session_break_resume_countdown():
    """A short warning countdown after a session break, used when no target
    window is locked. It gives the person a moment to click back into the right
    window before typing starts again. Returns False if Stop was requested."""
    for seconds_left in range(SESSION_BREAK_RESUME_SECONDS, 0, -1):
        if stop_event.is_set():
            return False
        state.status = f"Session break over, typing again in {seconds_left}s..."
        time.sleep(1)
        state.paused_duration += 1
    return True


def _wait_for_pause_marker(seconds):
    """Waits `seconds` for an inline {{pause:N}} marker, in small steps so
    Stop, Pause, and the activity auto-pause all still work during the
    wait. While paused, the countdown freezes and picks up where it left
    off. Returns True when the wait finished, or False if Stop was
    requested (the caller should return right away).
    Wait time is added to paused_duration so it is left out of the live
    WPM and ETA, the same way research breaks are."""
    remaining = float(seconds)
    last_tick = time.time()
    while remaining > 0:
        if stop_event.is_set():
            return False

        if pause_event.is_set():
            block_start = time.time()
            while pause_event.is_set():
                if stop_event.is_set():
                    return False
                if not state.status.startswith("Activity detected"):
                    state.status = "Paused"
                time.sleep(0.2)
            _note_blocked_time(time.time() - block_start)
            last_tick = time.time()
            continue

        state.status = f"Pause marker ({int(remaining + 0.999)}s left)..."
        time.sleep(min(0.1, remaining))
        now = time.time()
        elapsed = now - last_tick
        last_tick = now
        remaining -= elapsed
        state.paused_duration += elapsed
    return True


def type_text(settings, is_batch=False, is_last=True):
    """Background-thread entry point. Reads the input file, then
    types it out character by character with randomized delays,
    simulated mistakes, and research breaks, honoring stop/pause/
    focus-lock events throughout."""

    input_path = settings["input_path"]
    min_delay = settings["min_delay"]
    max_delay = settings["max_delay"]
    mistake_min = settings["mistake_min"]
    mistake_max = settings["mistake_max"]
    break_min = settings["break_min"]
    break_max = settings["break_max"]
    break_duration_min = settings["break_duration_min"]
    break_duration_max = settings["break_duration_max"]
    enable_mistakes = settings["enable_mistakes"]
    enable_breaks = settings["enable_breaks"]
    locked_window = settings["locked_window"]
    enable_typos = settings["enable_typos"]
    typo_chance = settings["typo_chance"]
    use_extra_typos = settings.get("use_extra_typos", False)
    paragraph_pause_min = settings["paragraph_pause_min"]
    paragraph_pause_max = settings["paragraph_pause_max"]
    sentence_pause_min = settings["sentence_pause_min"]
    sentence_pause_max = settings["sentence_pause_max"]
    enable_paraphrase = settings.get("enable_paraphrase", True)
    paraphrase_chance = settings.get("paraphrase_chance", 15.0)
    enable_fatigue = settings.get("enable_fatigue", False)
    fatigue_strength = settings.get("fatigue_strength", 15.0)
    enable_finger_timing = settings.get("enable_finger_timing", True)
    enable_typing_moods = settings.get("enable_typing_moods", True)
    typing_mood_strength = settings.get("typing_mood_strength", 100.0)
    enable_difficulty_pauses = settings.get("enable_difficulty_pauses", True)
    difficulty_pause_strength = settings.get("difficulty_pause_strength", 100.0)
    smart_fix_visible_chance = settings.get("smart_fix_visible_chance", 50.0)

    try:
        with open(input_path, "r", encoding="utf-8") as file:
            text = file.read()
    except FileNotFoundError:
        run_on_main_thread(lambda: _fail_and_stop(
            "Error", f"Could not find:\n{input_path}\n\nUse Browse to pick a valid text file."
        ))
        return
    except OSError as e:
        error_msg = str(e)
        run_on_main_thread(lambda: _fail_and_stop("Error", f"Could not read input file:\n{error_msg}"))
        return

    if not text:
        run_on_main_thread(lambda: _info_and_stop("Empty File", "The selected input file is empty \u2014 nothing to type."))
        return

    # Pause markers are removed here too, and smart fixes (when switched on)
    # are applied, so every position below (resume, protection, paraphrase,
    # pauses, fix actions) refers to the same final text.
    prepared = prepare_input_text(text, settings)
    text = prepared.clean_text
    protected_ranges = prepared.protected_ranges
    pause_map = prepared.pause_map
    format_map = prepared.format_map
    fix_actions = prepared.fix_actions
    state.clean_text = text  # stored so stop_typing() can compute the prefix hash
    protection_mask = build_protection_mask(len(text), protected_ranges)
    paraphrase_map = precompute_paragraph_paraphrases(text, protected_ranges)

    resume_from = min(settings.get("resume_from", 0), len(text))

    # Thinking pauses for hard words and hard sentences (Update #33). They are
    # planned only for text that is still to be typed. If planning ever fails,
    # the file is simply typed without them.
    difficulty_plan = {}
    if enable_difficulty_pauses and difficulty_pause_strength > 0:
        try:
            difficulty_plan = plan_difficulty_pauses(text, protection_mask, resume_from)
        except Exception:
            difficulty_plan = {}
            log_event("difficulty_pauses_failed", "could not plan thinking pauses; typing without them")

    # Mistakes to purposely leave for the person to fix (Update #24). They are
    # planned only for text that is still to be typed. If planning ever fails,
    # the file is simply typed without them.
    left_mistake_map = {}
    if get_left_mistake_options(settings) is not None:
        if resume_from > 0:
            state.left_mistakes_resumed = True
        try:
            # A format shortcut may not sit inside a two-character mistake, so
            # those spots are handled like pause markers.
            left_mistake_map = plan_left_mistakes(
                text, protection_mask, settings, resume_from,
                set(pause_map) | set(format_map), set(fix_actions.keys())
            )
        except Exception:
            left_mistake_map = {}
            log_event("leave_mistakes_failed", "could not plan mistakes; typing without them")

    state.total_chars = len(text)
    state.total_newlines = text.count("\n")
    state.chars_typed = resume_from
    state.newlines_typed = text[:resume_from].count("\n")
    state.paused_duration = 0.0
    state.status = "Countdown..."
    # Only the file's name goes in the log, never the full folder path.
    log_event("session_start", f"file={os.path.basename(input_path)}, chars={state.total_chars}, resumed_from={resume_from}")
    if prepared.fixes:
        log_event("smart_fixes_planned", summarize_smart_fixes(prepared.fixes))

    # 10 SECOND COUNTDOWN (skipped in preview mode -- there's no
    # target window to switch focus to before typing starts).
    if not settings.get("preview_mode"):
        for i in range(10, 0, -1):
            if stop_event.is_set():
                return
            state.status = f"Starting in {i}..."
            time.sleep(1)

    state.status = "Typing..."
    state.start_time = time.time()
    log_event("typing_start", "")

    # TARGET TIME (Update #27): plan the long session breaks. Preview runs
    # skip this so a preview never waits for hours. If planning ever fails,
    # the file is simply typed without session breaks.
    session_plan = []
    session_break_index = 0
    session_break_total = 0.0  # time spent in session breaks so far
    file_target_seconds = settings.get("target_seconds_for_file", 0.0)
    plan_start_time = time.time()
    plan_blocked_start = state.blocked_seconds
    state.target_end_time = 0.0
    state.session_breaks_left = 0
    if file_target_seconds > 0 and not settings.get("preview_mode"):
        try:
            session_plan, spare_seconds, typing_seconds = plan_session_breaks(
                text, protection_mask, pause_map, settings, resume_from, file_target_seconds
            )
        except Exception:
            session_plan = []
            spare_seconds = 0.0
            typing_seconds = 0.0
            log_event("target_time_failed", "could not plan session breaks; typing without them")
        state.target_end_time = plan_start_time + file_target_seconds
        state.session_breaks_left = len(session_plan)
        log_event(
            "target_time_plan",
            f"target={format_duration_text(file_target_seconds)}, "
            f"estimated typing={format_duration_text(typing_seconds)}, "
            f"spare={format_duration_text(max(spare_seconds, 0))}, breaks={len(session_plan)}",
        )

    word_count = 0
    para_count = 0
    next_mistake_target = random.randint(mistake_min, mistake_max) if enable_mistakes else None
    next_break_target = random.randint(break_min, break_max) if enable_breaks else None
    state.next_mistake_words = next_mistake_target if next_mistake_target else 0
    state.next_break_paragraphs = next_break_target if next_break_target else 0

    prev_char = text[resume_from - 1] if resume_from > 0 else ""
    last_focus_check = 0.0
    # Seed the rework buffer with real text already typed before this
    # point (back to the last paragraph break), so reworks right
    # after resuming still have real context instead of an empty one.
    last_newline_idx = text.rfind("\n", 0, resume_from)
    recent_typed_buffer = text[last_newline_idx + 1:resume_from] if last_newline_idx != -1 else text[:resume_from]
    if len(recent_typed_buffer) > 120:
        recent_typed_buffer = recent_typed_buffer[-120:]

    # Multiplier starts fresh on run start and resume-from-checkpoint
    fatigue_chars = 0

    # Flow and hesitation states (Update #31). None when switched off or at 0%.
    # Like fatigue, a resumed run starts in the normal state.
    typing_mood = TypingMood(typing_mood_strength) if enable_typing_moods and typing_mood_strength > 0 else None

    # Tracks when we last wrote an autosave checkpoint. Initialized to 0
    # so the first eligible sentence-end immediately saves.
    last_autosave_time = 0.0

    # Leave-mistakes bookkeeping. left_skip_until: a two-character mistake
    # (swapped letters) types both characters at once, so the loop skips the
    # second one. rework_hold: True from a left mistake until the next word
    # break, so a sentence rework never deletes or retypes the mistake.
    left_skip_until = 0
    rework_hold = False

    # Formatting markers (Update #29): the styles that are on right now. When
    # resuming, every shortcut before the resume point counts as already
    # pressed, so the person should click right after the last typed character.
    active_formats = set()
    for format_position in sorted(format_map):
        if format_position >= resume_from:
            break
        active_formats ^= set(format_map[format_position])

    # CHARACTER TYPING LOOP
    for idx in range(resume_from, len(text)):
        char = text[idx]
        is_protected = protection_mask[idx]
        if stop_event.is_set():
            return
        if idx < left_skip_until:
            continue

        # --------------------------------
        # SESSION BREAK (target time, Update #27)
        # A long break at a planned paragraph or sentence start. Its length
        # is worked out now from the time that is really left, so the run
        # finishes close to the target. Like the pause marker below, this runs
        # BEFORE the focus-lock check, so after a long wait the check runs
        # right away and catches a window switch before anything is typed.
        # --------------------------------
        if session_break_index < len(session_plan) and idx >= session_plan[session_break_index]["position"]:
            planned_break = session_plan[session_break_index]
            weights_left = [item["weight"] for item in session_plan[session_break_index:]]
            elapsed_active = (
                time.time() - plan_start_time - session_break_total
                - (state.blocked_seconds - plan_blocked_start)
            )
            break_seconds = compute_session_break_seconds(
                text, settings, protection_mask, pause_map, resume_from, idx,
                file_target_seconds, elapsed_active, session_break_total,
                planned_break["weight"], sum(weights_left),
            )
            session_break_index += 1
            state.session_breaks_left = len(session_plan) - session_break_index
            if break_seconds > 0:
                log_event(
                    "session_break_start",
                    f"~{format_duration_text(break_seconds)}, break {session_break_index} of {len(session_plan)}",
                )
                break_started = time.time()
                blocked_before_break = state.blocked_seconds
                break_finished = _wait_for_session_break(break_seconds, session_break_index, len(session_plan))
                if break_finished and not settings.get("preview_mode") and not (locked_window and HAS_PYGETWINDOW):
                    break_finished = _session_break_resume_countdown()
                # The person's own pauses during the break are counted separately.
                session_break_total += (time.time() - break_started) - (state.blocked_seconds - blocked_before_break)
                if not break_finished:
                    return
                log_event("session_break_end", "")
                if enable_fatigue:
                    fatigue_chars = 0  # a long rest fully recovers from fatigue
                if typing_mood is not None:
                    typing_mood.reset()  # and the typist starts fresh in the normal state
                state.status = "Typing..."
            else:
                log_event("session_break_skipped", "no spare time left for this break")

        # --------------------------------
        # INLINE PAUSE MARKER ({{pause:N}})
        # Runs BEFORE the focus-lock check on purpose: after a long
        # wait the focus check is overdue, so it runs right away and
        # catches a window switch before the next character is typed.
        # --------------------------------
        marker_seconds = pause_map.get(idx)
        if marker_seconds:
            log_event("pause_marker", f"{marker_seconds:g}s before character {idx}")
            if not _wait_for_pause_marker(marker_seconds):
                return
            state.status = "Typing..."

        # --------------------------------
        # DIFFICULTY THINKING PAUSE (Update #33)
        # A short stop before a hard word, or a longer one before a hard
        # sentence. Like the pause marker above, it runs BEFORE the focus-lock
        # and pause checks, so a Pause press or a window switch during the
        # wait is caught before the next character is typed. Only the spot
        # (never the text) goes in the log.
        # --------------------------------
        difficulty_spot = difficulty_plan.get(idx)
        if difficulty_spot:
            difficulty_kind, difficulty_weight = difficulty_spot
            think_seconds = draw_difficulty_pause_seconds(
                difficulty_kind, difficulty_weight, difficulty_pause_strength
            )
            if think_seconds > 0:
                if difficulty_kind == "sentence":
                    state.status = "Thinking about how to phrase this..."
                else:
                    state.status = "Thinking of the right word..."
                log_event("difficulty_pause", f"{difficulty_kind} ~{think_seconds:.1f}s before character {idx}")
                for _ in range(int(think_seconds * 10)):
                    if stop_event.is_set() or pause_event.is_set():
                        break
                    time.sleep(0.1)
                if stop_event.is_set():
                    return
                if not pause_event.is_set():
                    state.status = "Typing..."

        # --------------------------------
        # FOCUS-LOCK CHECK (throttled to ~2x/sec so we're not
        # calling into the OS window manager on every keystroke)
        # --------------------------------
        if locked_window and HAS_PYGETWINDOW:
            now = time.time()
            if now - last_focus_check > 0.5:
                last_focus_check = now
                active = gw.getActiveWindow()
                active_title = active.title if active else ""
                if active_title != locked_window:
                    if not focus_lost_event.is_set():
                        focus_lost_event.set()
                        log_event("focus_lost", f"active window now '{active_title}'")
                else:
                    if focus_lost_event.is_set():
                        focus_lost_event.clear()
                        log_event("focus_regained", "")

        # --------------------------------
        # PAUSE / FOCUS-LOST HANDLING
        # Blocks here until both conditions clear, then folds the
        # elapsed block time into paused_duration exactly once.
        # --------------------------------
        if pause_event.is_set() or focus_lost_event.is_set():
            block_start = time.time()
            while pause_event.is_set() or focus_lost_event.is_set():
                if stop_event.is_set():
                    return
                if focus_lost_event.is_set():
                    state.status = "Focus lost \u2014 paused"
                    if HAS_PYGETWINDOW:
                        active = gw.getActiveWindow()
                        active_title = active.title if active else ""
                        if active_title == locked_window:
                            focus_lost_event.clear()
                elif pause_event.is_set():
                    if not state.status.startswith("Activity detected"):
                        state.status = "Paused"
                time.sleep(0.2)
            _note_blocked_time(time.time() - block_start)
            state.status = "Typing..."

        # --------------------------------
        # PARAGRAPH-ENDING PARAPHRASE-THEN-CORRECT (Idea #10)
        # --------------------------------
        # Skipped while a style is on: deleting the drafted sentence could drop
        # the bold or italic from the cursor in some editors (Update #29).
        if enable_paraphrase and idx in paraphrase_map and not is_protected and not active_formats:
            candidate = paraphrase_map.pop(idx, None)
            if candidate and random.uniform(0, 100) < paraphrase_chance:
                _end_idx, paraphrased_text, original_text = candidate
                state.status = "Drafting paraphrase..."
                log_event("paraphrase_rework", f"drafted: '{paraphrased_text}'")

                for p_char in paraphrased_text:
                    if stop_event.is_set():
                        return
                    _send_char(settings, p_char)
                    p_delay = random.uniform(min_delay, max_delay)
                    if p_char.isalpha():
                        p_delay += random.uniform(0.01, 0.03)
                    if typing_mood is not None:
                        p_delay *= typing_mood.step()[0]
                    if enable_fatigue:
                        p_delay *= get_fatigue_multiplier(fatigue_chars, fatigue_strength)
                        fatigue_chars += 1
                    time.sleep(p_delay)

                state.status = "Reconsidering phrasing..."
                reconsider_pause = random.uniform(0.8, 1.8)
                for _ in range(int(reconsider_pause * 10)):
                    if stop_event.is_set():
                        return
                    time.sleep(0.1)

                state.status = "Deleting and rewriting..."
                p_words = [w for w in paraphrased_text.split() if w]
                for _ in range(len(p_words)):
                    if stop_event.is_set():
                        return
                    _send_ctrl_backspace(settings)
                    time.sleep(random.uniform(0.1, 0.25))

                time.sleep(random.uniform(0.3, 0.6))
                word_count = 0  # avoid immediately firing mistake rework
                state.status = "Typing..."

        # --------------------------------
        # MISTAKE SIMULATION: pause, reconsider, delete and retype
        # the last few words actually typed. Not gibberish -- this
        # mirrors how people really hesitate: they pause, backspace
        # what they just wrote, then type the same thing again
        # (the final text has to stay exactly correct either way).
        # --------------------------------
        if enable_mistakes and char == " " and not is_protected:
            word_count += 1
            if word_count >= next_mistake_target:
                words_in_buffer = [w for w in recent_typed_buffer.split(" ") if w]
                if rework_hold:
                    # A mistake was purposely left in the last few words.
                    # Never redo them, or the mistake would be erased.
                    words_in_buffer = []
                redo_count = min(random.randint(2, 4), len(words_in_buffer))
                if active_formats:
                    # Keep at least one word of the formatted text, so the first
                    # formatted character is never deleted (an editor could drop
                    # the style when that happens). Update #29.
                    redo_count = min(redo_count, len(words_in_buffer) - 1)
                if redo_count > 0:
                    redo_text = " ".join(words_in_buffer[-redo_count:])
                    state.status = "Reworking last words..."
                    log_event("sentence_rework", f"redid: '{redo_text}'")

                    time.sleep(random.uniform(0.6, 1.5))
                    for _ in range(redo_count):
                        _send_ctrl_backspace(settings)
                        time.sleep(random.uniform(0.1, 0.3))
                    time.sleep(random.uniform(0.3, 0.8))
                    _send_char(settings, redo_text)
                    time.sleep(random.uniform(0.2, 0.5))

                    state.status = "Typing..."

                word_count = 0
                next_mistake_target = random.randint(mistake_min, mistake_max)
                state.next_mistake_words = next_mistake_target
        if enable_mistakes:
            state.next_mistake_words = max(next_mistake_target - word_count, 0)

        # --------------------------------
        # PARAGRAPH THINKING PAUSE: always happens at the end of a
        # paragraph, independent of whether a research break is also
        # enabled or due -- pausing to think about what's next is
        # normal even when not stepping away entirely.
        # --------------------------------
        if char == "\n":
            thinking_pause = random.uniform(paragraph_pause_min, paragraph_pause_max)
            state.status = "Thinking about what's next..."
            for _ in range(int(thinking_pause * 10)):
                if stop_event.is_set() or pause_event.is_set():
                    break
                time.sleep(0.1)
            if stop_event.is_set():
                return
            if not pause_event.is_set():
                state.status = "Typing..."

        # --------------------------------
        # RESEARCH BREAKS
        # --------------------------------
        if enable_breaks and char == "\n":
            para_count += 1
            if para_count >= next_break_target:
                break_duration = random.uniform(break_duration_min, break_duration_max)
                state.status = f"Research Break ({int(break_duration)}s left)..."
                log_event("research_break_start", f"~{break_duration:.0f}s")
                break_start = time.time()

                total_ticks = int(break_duration * 10)
                for tick in range(total_ticks):
                    if stop_event.is_set() or pause_event.is_set():
                        break
                    if tick % 10 == 0:
                        remaining = max(0, int(break_duration) - (tick // 10))
                        state.status = f"Research Break ({remaining}s left)..."
                    time.sleep(0.1)

                state.paused_duration += time.time() - break_start
                log_event("research_break_end", "")

                if enable_fatigue:
                    # Partly recover from fatigue after a research break
                    fatigue_chars = int(fatigue_chars * 0.25)
                if typing_mood is not None:
                    typing_mood.reset()  # a research break also clears the mood

                para_count = 0
                next_break_target = random.randint(break_min, break_max)
                state.next_break_paragraphs = next_break_target
                state.status = "Typing..."
        if enable_breaks:
            state.next_break_paragraphs = max(next_break_target - para_count, 0)

        # --------------------------------
        # SMART FIX "NOTICED A SLIP" (Update #22)
        # Some planned fixes are acted out: the typer makes the slip,
        # pauses, and backspaces it before typing the correct text. The
        # chance comes from the 'Show fixes being noticed' setting. The
        # fixed text itself was already decided before typing began.
        # --------------------------------
        fix_acts = fix_actions.get(idx)
        if fix_acts and smart_fix_visible_chance > 0:
            for fix_kind, slip_text in fix_acts:
                if random.uniform(0, 100) >= smart_fix_visible_chance:
                    continue
                if not _act_out_smart_fix(settings, fix_kind, slip_text):
                    return
            state.status = "Typing..."

        # --------------------------------
        # TYPOS: adjacent-key errors, or expanded realistic typo types
        # (swapped letters, doubled letters, missed spaces) when enabled.
        # --------------------------------
        char_already_typed = False
        if enable_typos and not is_protected and idx not in left_mistake_map:
            can_typo = char.isalpha() or (use_extra_typos and char == " ")
            if can_typo and random.uniform(0, 100) < typo_chance:
                if not use_extra_typos:
                    if char.isalpha() and char.lower() in QWERTY_ADJACENCY:
                        _simulate_adjacent_key_typo(settings, char, text, idx, protection_mask, False)
                else:
                    candidates = []
                    if char.isalpha() and char.lower() in QWERTY_ADJACENCY:
                        candidates.append("adjacent")
                    if (char.isalpha()
                            and idx < len(text) - 1
                            and text[idx + 1].isalpha()
                            and not protection_mask[idx + 1]
                            and char != text[idx + 1]):
                        candidates.append("swap")
                    if char.isalpha() and idx not in format_map:
                        # (not at a format shortcut: the doubled letter stays on screen)
                        candidates.append("double")
                    if (char == " "
                            and idx > 0 and text[idx - 1].isalpha()
                            and idx + 1 < len(text) and text[idx + 1].isalpha()
                            and not protection_mask[idx + 1]):
                        candidates.append("missed_space")

                    if candidates:
                        chosen_typo = random.choice(candidates)
                        if chosen_typo == "adjacent":
                            _simulate_adjacent_key_typo(settings, char, text, idx, protection_mask, True)
                        elif chosen_typo == "swap":
                            _simulate_swapped_letter_typo(settings, text, idx, protection_mask)
                        elif chosen_typo == "double":
                            char_already_typed = _simulate_doubled_letter_typo(settings, text, idx, protection_mask)
                        elif chosen_typo == "missed_space":
                            _simulate_missed_space_typo(settings, text, idx, protection_mask)

        if stop_event.is_set():
            return

        # --------------------------------
        # LETTER-PAIR SPEED-UP + BASE DELAY
        # --------------------------------
        current_pair = (prev_char + char).lower()
        if current_pair in COMMON_PAIRS:
            delay = random.uniform(0.02, 0.05)
        else:
            delay = random.uniform(min_delay, max_delay)
            if char.isalpha():
                delay += random.uniform(0.01, 0.03)
            if enable_finger_timing:
                # Update #28: opposite hands a little faster, same finger a little slower
                delay *= get_finger_timing_multiplier(prev_char, char)

        if typing_mood is not None:
            # Update #31: flow makes the key quicker, hesitation slower (before fatigue)
            mood_multiplier, mood_started = typing_mood.step()
            delay *= mood_multiplier
            if mood_started in (MOOD_FLOW, MOOD_HESITANT):
                log_event("typing_mood", f"{mood_started} from character {idx}")

        if enable_fatigue:
            delay *= get_fatigue_multiplier(fatigue_chars, fatigue_strength)
            fatigue_chars += 1

        left_entry = left_mistake_map.get(idx)

        # --------------------------------
        # FORMATTING MARKERS ([[b]] / [[i]], Update #29)
        # Pressed right before the character is typed, AFTER any typo or
        # slip has been typed and backspaced. That order matters: backspacing
        # the first character after a shortcut could drop the style. The
        # rework buffer is cleared so a rework never reaches back across a
        # style change.
        # --------------------------------
        format_kinds = format_map.get(idx)
        if format_kinds:
            if not _press_format_toggles(settings, format_kinds, active_formats):
                return
            recent_typed_buffer = ""

        if left_entry is not None:
            # Purposely left mistake: type the wrong text instead of this
            # character (and skip any extra character it replaces).
            _type_left_mistake(settings, left_entry, input_path)
            left_skip_until = left_entry["end"]
            state.chars_typed += left_entry["end"] - idx - 1
            rework_hold = True
            recent_typed_buffer = ""
        elif not char_already_typed:
            _send_char(settings, char)
        state.chars_typed += 1
        if left_entry is not None:
            pass  # the rework buffer was already cleared above
        elif char == "\n":
            state.newlines_typed += 1
            recent_typed_buffer = ""
            rework_hold = False
        elif is_protected:
            # Never let a rework reach into or across protected text
            recent_typed_buffer = ""
        else:
            recent_typed_buffer += char
            if len(recent_typed_buffer) > 120:
                recent_typed_buffer = recent_typed_buffer[-120:]
            if rework_hold and char == " ":
                # First word break after a left mistake: start a fresh
                # buffer so reworks only ever redo whole, untouched words.
                recent_typed_buffer = ""
                rework_hold = False

        # AUTOSAVE CHECKPOINT: after a settled sentence-end position.
        # Newlines and sentence-enders are points where no typo, rework, or
        # paraphrase is partially done, so they are safe to resume from.
        # We skip this in preview mode (never touch resume state there).
        if (not settings.get("preview_mode")
                and char in (".", "!", "?", "\n")
                and time.time() - last_autosave_time >= 5.0):
            autosave_idx = idx + 1  # characters typed through this index
            autosave_hash = hash_clean_prefix(text, autosave_idx)
            save_resume_state(input_path, autosave_idx, len(text), autosave_hash)
            last_autosave_time = time.time()

        prev_char = char
        time.sleep(delay)

        if char in [",", ";", ":"]:
            time.sleep(random.uniform(0.5, 1.5))
        elif char in [".", "!", "?"]:
            sentence_pause = random.uniform(sentence_pause_min, sentence_pause_max)
            state.status = "Pausing after sentence..."
            for _ in range(int(sentence_pause * 10)):
                if stop_event.is_set() or pause_event.is_set():
                    break
                time.sleep(0.1)
            if stop_event.is_set():
                return
            if not pause_event.is_set():
                state.status = "Typing..."

    # Formatting shortcuts at the very end of the file (this includes the
    # automatic close of any style left open) come after the last character.
    trailing_format = format_map.get(len(text))
    if trailing_format and not _press_format_toggles(settings, trailing_format, active_formats):
        return

    # A {{pause:N}} marker at the very end of the file waits after the last character.
    trailing_pause = pause_map.get(len(text))
    if trailing_pause:
        log_event("pause_marker", f"{trailing_pause:g}s after the last character")
        if not _wait_for_pause_marker(trailing_pause):
            return

    log_event("session_end", "finished")
    if is_last:
        state.status = "Finished!"
        run_on_main_thread(stop_typing)
    else:
        clear_resume_state(input_path)
        if state.logging_enabled and state.log_entries:
            export_log(auto=True)
            state.log_entries = []


# --------------------------------------------
# GUI AND THREAD CONTROLS
# --------------------------------------------
def estimate_smart_fix_seconds(prepared, settings, start_index=0):
    """Rough extra time for the smart fixes that will be acted out (typed
    wrong, noticed, backspaced) from start_index onward. Each one costs
    about a second, and only the 'Show fixes being noticed' share is acted out."""
    chance = settings.get("smart_fix_visible_chance", 0.0)
    if not prepared.fix_actions or chance <= 0:
        return 0.0
    acted_count = sum(len(acts) for index, acts in prepared.fix_actions.items() if index >= start_index)
    return acted_count * (chance / 100.0) * 1.0


def estimate_format_toggle_seconds(prepared, start_index=0):
    """Rough time for the bold and italic shortcuts (Update #29) from
    start_index onward. Each press takes about a third of a second."""
    press_count = sum(len(kinds) for index, kinds in prepared.format_map.items() if index >= start_index)
    return press_count * FORMAT_TOGGLE_ESTIMATE_SECONDS


def estimate_typing_time(text, settings, protection_mask=None, marker_pause_seconds=0.0, extra_seconds=0.0):
    """Rough estimate of total run time for the given text under the
    given settings, using averages of the same ranges type_text()
    actually draws from. This is an estimate, not a simulation --
    randomness means an actual run will vary somewhat."""
    char_count = len(text)
    word_count = len([w for w in text.split() if w])

    avg_delay = (settings["min_delay"] + settings["max_delay"]) / 2 + 0.015
    total_seconds = char_count * avg_delay

    other_punct_count = sum(text.count(c) for c in [",", ";", ":"])
    total_seconds += other_punct_count * 1.0  # avg of the fixed 0.5-1.5s pause

    sentence_ender_count = sum(text.count(c) for c in [".", "!", "?"])
    avg_sentence_pause = (settings["sentence_pause_min"] + settings["sentence_pause_max"]) / 2
    total_seconds += sentence_ender_count * avg_sentence_pause

    newline_count = text.count("\n")
    avg_paragraph_pause = (settings["paragraph_pause_min"] + settings["paragraph_pause_max"]) / 2
    total_seconds += newline_count * avg_paragraph_pause

    if settings["enable_breaks"] and settings["break_min"] and settings["break_max"]:
        avg_break_interval = (settings["break_min"] + settings["break_max"]) / 2
        avg_break_duration = (settings["break_duration_min"] + settings["break_duration_max"]) / 2
        estimated_breaks = newline_count / avg_break_interval if avg_break_interval > 0 else 0
        total_seconds += estimated_breaks * avg_break_duration

    if settings["enable_mistakes"] and settings["mistake_min"] and settings["mistake_max"]:
        avg_mistake_interval = (settings["mistake_min"] + settings["mistake_max"]) / 2
        estimated_reworks = word_count / avg_mistake_interval if avg_mistake_interval > 0 else 0
        total_seconds += estimated_reworks * 3.5  # rough overhead per rework, pauses + retyping

    if settings["enable_typos"] and settings["typo_chance"] > 0:
        if protection_mask is not None:
            alpha_count = sum(1 for i, c in enumerate(text) if c.isalpha() and not protection_mask[i])
        else:
            alpha_count = sum(1 for c in text if c.isalpha())
        estimated_typos = alpha_count * (settings["typo_chance"] / 100)
        overhead = 0.7 if settings.get("use_extra_typos") else 0.25
        total_seconds += estimated_typos * overhead

    if settings.get("enable_paraphrase") and settings.get("paraphrase_chance", 0) > 0:
        estimated_paraphrases = newline_count * (settings["paraphrase_chance"] / 100)
        total_seconds += estimated_paraphrases * 4.0

    if settings.get("enable_finger_timing", True):
        # Finger and hand timing (Update #28): hand switches are a little faster and
        # same-finger pairs a little slower. The shift is counted in average delays.
        total_seconds += estimate_finger_timing_shift(text) * avg_delay

    # Thinking pauses on hard words and hard sentences (Update #33), averaged
    # over the same chances and ranges the typing loop draws from.
    total_seconds += estimate_difficulty_pause_seconds(text, protection_mask, settings)

    if settings.get("enable_fatigue") and settings.get("fatigue_strength", 0) > 0:
        # Fatigue drift approximate adjustment: as characters are typed, delays gradually
        # increase up to fatigue_strength percent and partly recover after research breaks.
        # Across a full session, the average per-character slowdown is approximately half
        # of the maximum fatigue strength setting. (Note: this is an approximation).
        fatigue_strength = settings["fatigue_strength"]
        avg_fatigue_mult = 1.0 + (fatigue_strength / 200.0)
        total_seconds += (char_count * avg_delay) * (avg_fatigue_mult - 1.0)

    if settings.get("enable_typing_moods", True) and settings.get("typing_mood_strength", 100.0) > 0:
        # Flow and hesitation states (Update #31): the average speed change over a
        # long run (a small net number, since flow and hesitation roughly balance).
        mood_shift = estimate_mood_multiplier(settings["typing_mood_strength"]) - 1.0
        total_seconds += (char_count * avg_delay) * mood_shift

    # Total of all inline {{pause:N}} markers still ahead (exact, not an average).
    total_seconds += marker_pause_seconds

    # Extra time for smart fixes that get acted out (see estimate_smart_fix_seconds).
    total_seconds += extra_seconds

    return word_count, char_count, total_seconds


def validate_settings():
    """Validate all numeric GUI inputs before a run starts. Returns
    a populated settings dict on success, or None (after showing an
    error) on failure."""
    try:
        min_delay = float(min_delay_var.get())
        max_delay = float(max_delay_var.get())
    except ValueError:
        messagebox.showerror("Invalid Input", "Min/Max delay must be numbers.")
        return None
    if min_delay < 0 or max_delay < 0:
        messagebox.showerror("Invalid Input", "Delays cannot be negative.")
        return None
    if min_delay > max_delay:
        messagebox.showerror("Invalid Input", "Min Delay cannot be greater than Max Delay.")
        return None

    try:
        paragraph_pause_min = float(paragraph_pause_min_var.get())
        paragraph_pause_max = float(paragraph_pause_max_var.get())
    except ValueError:
        messagebox.showerror("Invalid Input", "Paragraph pause (seconds) must be numbers.")
        return None
    if paragraph_pause_min < 0 or paragraph_pause_max < 0:
        messagebox.showerror("Invalid Input", "Paragraph pause cannot be negative.")
        return None
    if paragraph_pause_min > paragraph_pause_max:
        messagebox.showerror("Invalid Input", "Min paragraph pause cannot exceed max paragraph pause.")
        return None

    try:
        sentence_pause_min = float(sentence_pause_min_var.get())
        sentence_pause_max = float(sentence_pause_max_var.get())
    except ValueError:
        messagebox.showerror("Invalid Input", "Sentence-end pause (seconds) must be numbers.")
        return None
    if sentence_pause_min < 0 or sentence_pause_max < 0:
        messagebox.showerror("Invalid Input", "Sentence-end pause cannot be negative.")
        return None
    if sentence_pause_min > sentence_pause_max:
        messagebox.showerror("Invalid Input", "Min sentence-end pause cannot exceed max sentence-end pause.")
        return None

    enable_mistakes = enable_mistakes_var.get()
    mistake_min = mistake_max = 0
    if enable_mistakes:
        try:
            mistake_min = int(mistake_min_var.get())
            mistake_max = int(mistake_max_var.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Mistake frequency (words) must be whole numbers.")
            return None
        if mistake_min < 1 or mistake_max < 1:
            messagebox.showerror("Invalid Input", "Mistake frequency must be at least 1 word.")
            return None
        if mistake_min > mistake_max:
            messagebox.showerror("Invalid Input", "Min mistake words cannot exceed max mistake words.")
            return None

    enable_breaks = enable_breaks_var.get()
    break_min = break_max = 0
    break_duration_min = break_duration_max = 0.0
    if enable_breaks:
        try:
            break_min = int(break_min_var.get())
            break_max = int(break_max_var.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Break frequency (paragraphs) must be whole numbers.")
            return None
        if break_min < 1 or break_max < 1:
            messagebox.showerror("Invalid Input", "Break frequency must be at least 1 paragraph.")
            return None
        if break_min > break_max:
            messagebox.showerror("Invalid Input", "Min break paragraphs cannot exceed max break paragraphs.")
            return None

        try:
            break_duration_min = float(break_duration_min_var.get())
            break_duration_max = float(break_duration_max_var.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Break duration (seconds) must be numbers.")
            return None
        if break_duration_min < 1 or break_duration_max < 1:
            messagebox.showerror("Invalid Input", "Break duration must be at least 1 second.")
            return None
        if break_duration_min > break_duration_max:
            messagebox.showerror("Invalid Input", "Min break duration cannot exceed max break duration.")
            return None

    queue_files = get_queue_files()
    input_path = input_path_var.get().strip()
    if not queue_files and not input_path:
        messagebox.showerror("Invalid Input", "Please choose an input text file or add files to the queue.")
        return None

    enable_typos = enable_typos_var.get()
    use_extra_typos = bool(use_extra_typos_var.get())
    typo_chance = 0.0
    if enable_typos:
        try:
            typo_chance = float(typo_chance_var.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Typo chance must be a number.")
            return None
        if typo_chance < 0 or typo_chance > 100:
            messagebox.showerror("Invalid Input", "Typo chance must be between 0 and 100.")
            return None

    enable_paraphrase = enable_paraphrase_var.get()
    paraphrase_chance = 0.0
    if enable_paraphrase:
        try:
            paraphrase_chance = float(paraphrase_chance_var.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Paraphrase chance must be a number.")
            return None
        if paraphrase_chance < 0 or paraphrase_chance > 100:
            messagebox.showerror("Invalid Input", "Paraphrase chance must be between 0 and 100.")
            return None

    locked_window = window_var.get().strip() if lock_window_var.get() else ""

    # Auto Profile by Window (Update #30). The rules are cleaned on the way in,
    # so anything malformed has already been dropped.
    enable_auto_profile = bool(enable_auto_profile_var.get())
    auto_profile_rules = get_auto_profile_rules()

    auto_pause_activity = auto_pause_activity_var.get()
    try:
        activity_sensitivity = float(activity_sensitivity_var.get())
        if activity_sensitivity < 5 or activity_sensitivity > 500:
            activity_sensitivity = 20.0
            activity_sensitivity_var.set("20")
    except ValueError:
        activity_sensitivity = 20.0
        activity_sensitivity_var.set("20")

    enable_fatigue = enable_fatigue_var.get()
    try:
        fatigue_strength = float(fatigue_strength_var.get())
        if fatigue_strength < 0:
            fatigue_strength = 0.0
            fatigue_strength_var.set("0")
        elif fatigue_strength > 40:
            fatigue_strength = 40.0
            fatigue_strength_var.set("40")
    except ValueError:
        fatigue_strength = 15.0
        fatigue_strength_var.set("15")

    enable_finger_timing = bool(enable_finger_timing_var.get())
    enable_typing_moods = bool(enable_typing_moods_var.get())
    try:
        typing_mood_strength = float(typing_mood_strength_var.get())
        if typing_mood_strength < 0:
            typing_mood_strength = 0.0
            typing_mood_strength_var.set("0")
        elif typing_mood_strength > MOOD_STRENGTH_MAX:
            typing_mood_strength = MOOD_STRENGTH_MAX
            typing_mood_strength_var.set(f"{MOOD_STRENGTH_MAX:g}")
    except ValueError:
        typing_mood_strength = 100.0
        typing_mood_strength_var.set("100")
    enable_difficulty_pauses = bool(enable_difficulty_pauses_var.get())
    try:
        difficulty_pause_strength = float(difficulty_pause_strength_var.get())
        if difficulty_pause_strength < 0:
            difficulty_pause_strength = 0.0
            difficulty_pause_strength_var.set("0")
        elif difficulty_pause_strength > DIFFICULTY_STRENGTH_MAX:
            difficulty_pause_strength = DIFFICULTY_STRENGTH_MAX
            difficulty_pause_strength_var.set(f"{DIFFICULTY_STRENGTH_MAX:g}")
    except ValueError:
        difficulty_pause_strength = 100.0
        difficulty_pause_strength_var.set("100")
    enable_pause_markers = bool(enable_pause_markers_var.get())
    enable_format_markers = bool(enable_format_markers_var.get())

    enable_smart_fixes = bool(enable_smart_fixes_var.get())
    smart_fix_spacing = bool(smart_fix_spacing_var.get())
    smart_fix_punctuation = bool(smart_fix_punctuation_var.get())
    smart_fix_capitalization = bool(smart_fix_capitalization_var.get())
    smart_fix_repeated_words = bool(smart_fix_repeated_words_var.get())
    try:
        smart_fix_visible_chance = float(smart_fix_visible_chance_var.get())
        if not (smart_fix_visible_chance >= 0):  # also catches NaN
            smart_fix_visible_chance = 0.0
            smart_fix_visible_chance_var.set("0")
        elif smart_fix_visible_chance > 100:
            smart_fix_visible_chance = 100.0
            smart_fix_visible_chance_var.set("100")
    except ValueError:
        smart_fix_visible_chance = 50.0
        smart_fix_visible_chance_var.set("50")

    enable_leave_mistakes = bool(enable_leave_mistakes_var.get())
    leave_mistakes_typos = bool(leave_mistakes_typos_var.get())
    leave_mistakes_commas = bool(leave_mistakes_commas_var.get())
    leave_mistakes_punctuation = bool(leave_mistakes_punctuation_var.get())
    try:
        leave_mistake_count = int(leave_mistake_count_var.get())
        if leave_mistake_count < 1:
            leave_mistake_count = 1
            leave_mistake_count_var.set("1")
        elif leave_mistake_count > LEFT_MISTAKE_MAX_COUNT:
            leave_mistake_count = LEFT_MISTAKE_MAX_COUNT
            leave_mistake_count_var.set(str(LEFT_MISTAKE_MAX_COUNT))
    except ValueError:
        leave_mistake_count = LEFT_MISTAKE_DEFAULT_COUNT
        leave_mistake_count_var.set(str(LEFT_MISTAKE_DEFAULT_COUNT))
    if enable_leave_mistakes and not (
        leave_mistakes_typos or leave_mistakes_commas or leave_mistakes_punctuation
    ):
        messagebox.showerror(
            "Invalid Input",
            "Turn on at least one kind of mistake to leave (typos, commas, or punctuation), "
            "or turn off 'Leave some mistakes for me to fix'.",
        )
        return None

    enable_target_time = bool(enable_target_time_var.get())
    target_seconds = 0.0
    if enable_target_time:
        try:
            target_hours = int(target_hours_var.get())
            target_minutes = int(target_minutes_var.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Target time hours and minutes must be whole numbers.")
            return None
        if target_hours < 0 or target_minutes < 0 or target_minutes > 59:
            messagebox.showerror(
                "Invalid Input",
                "Target time hours cannot be negative, and minutes must be between 0 and 59.",
            )
            return None
        if target_hours > TARGET_TIME_MAX_HOURS:
            messagebox.showerror(
                "Invalid Input",
                f"Target time cannot be longer than {TARGET_TIME_MAX_HOURS} hours.",
            )
            return None
        target_seconds = float(target_hours * 3600 + target_minutes * 60)
        if target_seconds < TARGET_TIME_MIN_SECONDS or target_seconds > TARGET_TIME_MAX_HOURS * 3600:
            messagebox.showerror(
                "Invalid Input",
                f"Target time must be between 1 minute and {TARGET_TIME_MAX_HOURS} hours.",
            )
            return None

    enable_update_check = bool(enable_update_check_var.get())
    skipped_update_version = skipped_update_version_var.get().strip()
    if skipped_update_version and parse_version_text(skipped_update_version) is None:
        skipped_update_version = ""
        skipped_update_version_var.set("")

    theme = theme_var.get().strip().lower()
    if theme not in ("light", "dark"):
        theme = "light"

    return {
        "input_path": input_path if not queue_files else queue_files[0],
        "file_queue": queue_files,
        "min_delay": min_delay,
        "max_delay": max_delay,
        "mistake_min": mistake_min,
        "mistake_max": mistake_max,
        "break_min": break_min,
        "break_max": break_max,
        "break_duration_min": break_duration_min,
        "break_duration_max": break_duration_max,
        "enable_mistakes": enable_mistakes,
        "enable_breaks": enable_breaks,
        "locked_window": locked_window,
        "enable_auto_profile": enable_auto_profile,
        "auto_profile_rules": auto_profile_rules,
        "auto_pause_activity": auto_pause_activity,
        "activity_sensitivity": activity_sensitivity,
        "enable_fatigue": enable_fatigue,
        "fatigue_strength": fatigue_strength,
        "enable_finger_timing": enable_finger_timing,
        "enable_typing_moods": enable_typing_moods,
        "typing_mood_strength": typing_mood_strength,
        "enable_difficulty_pauses": enable_difficulty_pauses,
        "difficulty_pause_strength": difficulty_pause_strength,
        "enable_target_time": enable_target_time,
        "target_seconds": target_seconds,
        "enable_pause_markers": enable_pause_markers,
        "enable_format_markers": enable_format_markers,
        "enable_smart_fixes": enable_smart_fixes,
        "smart_fix_spacing": smart_fix_spacing,
        "smart_fix_punctuation": smart_fix_punctuation,
        "smart_fix_capitalization": smart_fix_capitalization,
        "smart_fix_repeated_words": smart_fix_repeated_words,
        "smart_fix_visible_chance": smart_fix_visible_chance,
        "enable_leave_mistakes": enable_leave_mistakes,
        "leave_mistake_count": leave_mistake_count,
        "leave_mistakes_typos": leave_mistakes_typos,
        "leave_mistakes_commas": leave_mistakes_commas,
        "leave_mistakes_punctuation": leave_mistakes_punctuation,
        "enable_update_check": enable_update_check,
        "skipped_update_version": skipped_update_version,
        "enable_typos": enable_typos,
        "typo_chance": typo_chance,
        "use_extra_typos": use_extra_typos,
        "enable_paraphrase": enable_paraphrase,
        "paraphrase_chance": paraphrase_chance,
        "paragraph_pause_min": paragraph_pause_min,
        "paragraph_pause_max": paragraph_pause_max,
        "sentence_pause_min": sentence_pause_min,
        "sentence_pause_max": sentence_pause_max,
        "theme": theme,
    }


TARGET_TIME_TOO_SHORT_FACTOR = 1.05  # typing estimate over the target by more than 5% counts as too short


def describe_target_time_plan(settings, estimated_seconds):
    """A short phrase for the pre-start estimate that says what the target
    time will do. Returns an empty string when no target time is set."""
    if not settings.get("enable_target_time"):
        return ""
    target = settings.get("target_seconds", 0.0)
    target_text = format_duration_text(target)
    if estimated_seconds > target * TARGET_TIME_TOO_SHORT_FACTOR:
        return f" \u2022 Target {target_text} is shorter than the typing time, so it cannot be reached"
    spare_seconds = target - estimated_seconds
    break_count = plan_session_break_count(spare_seconds)
    if break_count == 0:
        return f" \u2022 Target {target_text}: no long breaks needed"
    return (
        f" \u2022 Target {target_text}: about {format_duration_text(spare_seconds)} "
        f"of long breaks (~{break_count} breaks)"
    )


def estimate_total_active_seconds(files, settings):
    """Rough typing time (without session breaks) for the text still to be
    typed in `files`, counting from any saved resume point. Returns None when a
    file cannot be read; type_text() reports unreadable files itself."""
    total_seconds = 0.0
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except OSError:
            return None
        if not raw_text:
            continue
        prepared = prepare_input_text(raw_text, settings)
        text = prepared.clean_text
        protection_mask = build_protection_mask(len(text), prepared.protected_ranges)
        resume_chars = load_resume_state_for(fpath) or 0
        total_seconds += estimate_active_seconds(
            text, settings, protection_mask, prepared.pause_map, resume_chars, len(text)
        )
        total_seconds += estimate_smart_fix_seconds(prepared, settings, resume_chars)
        total_seconds += estimate_format_toggle_seconds(prepared, resume_chars)
    return total_seconds


def confirm_target_time_plan(files, settings):
    """When a target time is set but is shorter than the estimated typing time,
    says so BEFORE typing starts, because the target cannot be reached.
    Returns True if it is OK to start, False if the person chose to change
    the settings first."""
    if not settings.get("enable_target_time"):
        return True
    estimated_seconds = estimate_total_active_seconds(files, settings)
    if estimated_seconds is None:
        return True  # unreadable files are reported when typing starts
    target = settings.get("target_seconds", 0.0)
    if estimated_seconds <= target * TARGET_TIME_TOO_SHORT_FACTOR:
        return True
    return messagebox.askokcancel(
        "Target Time Too Short",
        f"Your target time is {format_duration_text(target)}, but typing this text with your "
        f"current speed and pause settings is estimated to take about "
        f"{format_duration_text(estimated_seconds)}.\n\n"
        "Amanuensis never types faster than your speed settings, so no long breaks will be "
        "added and the run will take about the estimated time.\n\n"
        "Click OK to start anyway, or Cancel to raise the target time or speed up your "
        "typing settings first.",
    )


def preview_estimate():
    """Reads the current input file(s) and settings (reusing the same
    validation as an actual run) and shows a word count and rough
    total-time estimate without starting anything."""
    settings = validate_settings()
    if settings is None:
        return

    queue_files = settings.get("file_queue", [])
    if queue_files:
        total_words = 0
        total_chars = 0
        total_seconds = 0.0
        total_fixes = 0
        for fpath in queue_files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError as e:
                error_msg = str(e)
                messagebox.showerror("Error", f"Could not read input file:\n{fpath}\n\n{error_msg}")
                return
            if not text:
                continue
            prepared = prepare_input_text(text, settings)
            text = prepared.clean_text
            pause_map = prepared.pause_map
            protection_mask = build_protection_mask(len(text), prepared.protected_ranges)
            resume_chars = load_resume_state_for(fpath)
            remaining_text = text[resume_chars:] if resume_chars else text
            remaining_mask = protection_mask[resume_chars:] if resume_chars else protection_mask
            marker_seconds = sum(secs for pos, secs in pause_map.items() if pos >= (resume_chars or 0))
            extra_seconds = (estimate_smart_fix_seconds(prepared, settings, resume_chars or 0)
                             + estimate_format_toggle_seconds(prepared, resume_chars or 0))
            w_count, c_count, est_sec = estimate_typing_time(
                remaining_text, settings, remaining_mask, marker_seconds, extra_seconds
            )
            total_fixes += len(prepared.fixes)
            total_words += w_count
            total_chars += c_count
            total_seconds += est_sec + 10.0  # 10s countdown allowance per file

        if total_chars == 0:
            preview_result_var.set("All queued files are empty \u2014 nothing to preview.")
            return

        est_mins = int(total_seconds // 60)
        est_secs = int(total_seconds % 60)
        fixes_note = f" \u2022 {total_fixes:,} smart fixes" if total_fixes else ""
        preview_result_var.set(
            f"Queue ({len(queue_files)} files): ~{total_words:,} words \u2022 {total_chars:,} characters{fixes_note} \u2022 "
            f"Estimated time: ~{est_mins}m {est_secs}s (rough estimate)"
            f"{describe_target_time_plan(settings, total_seconds)}"
        )
        return

    try:
        with open(settings["input_path"], "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        error_msg = str(e)
        messagebox.showerror("Error", f"Could not read input file:\n{error_msg}")
        return

    if not text:
        preview_result_var.set("The selected file is empty \u2014 nothing to preview.")
        return

    prepared = prepare_input_text(text, settings)
    text = prepared.clean_text
    pause_map = prepared.pause_map
    protection_mask = build_protection_mask(len(text), prepared.protected_ranges)

    resume_chars = load_resume_state_for(settings["input_path"])
    remaining_text = text[resume_chars:] if resume_chars else text
    remaining_mask = protection_mask[resume_chars:] if resume_chars else protection_mask
    marker_seconds = sum(secs for pos, secs in pause_map.items() if pos >= (resume_chars or 0))
    extra_seconds = (estimate_smart_fix_seconds(prepared, settings, resume_chars or 0)
                     + estimate_format_toggle_seconds(prepared, resume_chars or 0))

    word_count, char_count, est_seconds = estimate_typing_time(
        remaining_text, settings, remaining_mask, marker_seconds, extra_seconds
    )
    est_mins = int(est_seconds // 60)
    est_secs = int(est_seconds % 60)
    resume_note = " remaining (resume point saved)" if resume_chars else ""
    fixes_note = f" \u2022 {len(prepared.fixes):,} smart fixes" if prepared.fixes else ""
    preview_result_var.set(
        f"{word_count:,} words \u2022 {char_count:,} characters{resume_note}{fixes_note} \u2022 "
        f"Estimated time: ~{est_mins}m {est_secs}s (rough estimate)"
        f"{describe_target_time_plan(settings, est_seconds)}"
    )


def notify_finished():
    """Called on the main thread when a run finishes naturally
    (never on a manual Stop). Notification and sound are independent
    toggles -- either, both, or neither can be on."""
    if enable_sound_var.get() and HAS_WINSOUND:
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
    if enable_notify_var.get():
        messagebox.showinfo("Amanuensis", "Typing session finished!")


def split_target_time_across_files(settings, files, resume_dict):
    """Returns one target time (in seconds) for each file in `files`, in the
    same order. With one file it is the whole target. With a batch queue the
    target is for the whole run, shared between the files by how much text
    each one still has to type. Returns zeros when no target time is set or
    when the run is a preview."""
    if not settings.get("enable_target_time") or settings.get("preview_mode"):
        return [0.0] * len(files)
    total_target = settings.get("target_seconds", 0.0)
    if len(files) == 1:
        return [total_target]
    remaining_chars = []
    for fpath in files:
        clean_text = get_clean_text_for_file(fpath, settings)
        if clean_text is None:
            remaining_chars.append(0)
        else:
            remaining_chars.append(max(len(clean_text) - resume_dict.get(fpath, 0), 0))
    total_chars = sum(remaining_chars)
    if total_chars <= 0:
        return [0.0] * len(files)
    return [total_target * chars / total_chars for chars in remaining_chars]


def run_typing_worker(settings, files, resume_dict):
    """Processes a list of files sequentially. Each file runs through
    type_text() completely (including its own 10-second countdown).
    Stops if stop_event is set or if typing is no longer running."""
    total_files = len(files)
    target_shares = split_target_time_across_files(settings, files, resume_dict)
    for idx, file_path in enumerate(files):
        if stop_event.is_set() or not state.is_running:
            break

        file_settings = dict(settings)
        file_settings["input_path"] = file_path
        file_settings["resume_from"] = resume_dict.get(file_path, 0)
        file_settings["target_seconds_for_file"] = target_shares[idx]
        state.settings = file_settings

        is_last = (idx == total_files - 1)
        type_text(file_settings, is_batch=(total_files > 1), is_last=is_last)


def confirm_pause_marker_warnings(files, settings):
    """Checks every file about to be typed for inline pause markers that
    have a problem, BEFORE typing starts: a bad value such as
    {{pause:abc}} (typed as normal text) or a value over the maximum
    (shortened). If there is anything to report, asks the person whether
    to start anyway. Returns True if it is OK to start, False if they
    chose to go fix the file first."""
    if not settings.get("enable_pause_markers", True):
        return True

    problems = []
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except OSError:
            continue  # type_text() already reports unreadable files
        _stripped, _pauses, invalid_markers, clamped_markers = strip_pause_markers(raw_text)
        file_name = os.path.basename(fpath)
        for marker in invalid_markers[:5]:
            problems.append(f"{file_name}: {marker}  (invalid, will be typed as normal text)")
        if len(invalid_markers) > 5:
            problems.append(f"{file_name}: ...and {len(invalid_markers) - 5} more invalid markers")
        for marker in clamped_markers[:5]:
            problems.append(
                f"{file_name}: {marker}  (over {int(PAUSE_MARKER_MAX_SECONDS)}s, will be shortened)"
            )
        if len(clamped_markers) > 5:
            problems.append(f"{file_name}: ...and {len(clamped_markers) - 5} more over-limit markers")

    if not problems:
        return True

    return messagebox.askokcancel(
        "Pause Marker Warning",
        "Some pause markers need attention:\n\n"
        + "\n".join(problems)
        + "\n\nA valid marker looks like {{pause:5}} (seconds, decimals allowed).\n\n"
        "Click OK to start anyway, or Cancel to fix the file first.",
    )


def confirm_format_marker_warnings(files, settings):
    """Update #29: checks every file about to be typed for bold or italic
    markers with no partner (a stray [[/b]], a repeated [[b]], or one that is
    never closed), BEFORE typing starts. Nothing is typed as text either way,
    but the person may have made a mistake. Returns True if it is OK to start,
    False if they chose to go fix the file first."""
    if not settings.get("enable_format_markers", True):
        return True

    problems = []
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except OSError:
            continue  # type_text() already reports unreadable files
        _stripped, _positions, _spans, file_problems = strip_format_markers(raw_text)
        file_name = os.path.basename(fpath)
        for problem in file_problems[:5]:
            problems.append(f"{file_name}: {problem}")
        if len(file_problems) > 5:
            problems.append(f"{file_name}: ...and {len(file_problems) - 5} more")

    if not problems:
        return True

    return messagebox.askokcancel(
        "Formatting Marker Warning",
        "Some bold or italic markers need attention:\n\n"
        + "\n".join(problems)
        + "\n\nA pair looks like [[b]]bold text[[/b]] or [[i]]italic text[[/i]].\n\n"
        "Click OK to start anyway, or Cancel to fix the file first.",
    )


def start_typing():
    if state.is_running:
        messagebox.showinfo("Already Running", "The typer is already running.")
        return

    # Auto Profile by Window (Update #30): this may load a saved profile into
    # the settings, so it must run before the settings are checked below.
    keep_starting, auto_profile_used = apply_auto_profile_for_start()
    if not keep_starting:
        return

    settings = validate_settings()
    if settings is None:
        return

    save_last_settings()

    queue_files = settings.get("file_queue", [])
    files_to_run = queue_files if queue_files else [settings["input_path"]]

    if not confirm_pause_marker_warnings(files_to_run, settings):
        return

    if not confirm_format_marker_warnings(files_to_run, settings):
        return

    if not confirm_target_time_plan(files_to_run, settings):
        return

    resume_dict = {}
    for fpath in files_to_run:
        entry = load_resume_entry(fpath)
        if entry is None:
            continue

        chars_typed = entry.get("chars_typed", 0)
        if chars_typed <= 0:
            continue

        # Get the clean text so we can validate and compute percent.
        clean_text = get_clean_text_for_file(fpath, settings)
        if clean_text is None:
            # File unreadable; type_text() will report the error.
            continue

        # Entry is at or past the end of the file — clear it and start fresh.
        if chars_typed >= len(clean_text):
            clear_resume_state(fpath)
            continue

        # Validate: check that the typed prefix hasn't changed.
        if not validate_resume_entry(entry, clean_text):
            file_name = os.path.basename(fpath)
            file_label = f"'{file_name}'" if len(files_to_run) > 1 else "this file"
            if not messagebox.askokcancel(
                "Saved Position Cannot Be Trusted",
                f"The part of {file_label} that was already typed has changed since "
                f"the progress was saved, so the saved spot cannot be trusted.\n\n"
                "(This also happens if Smart Text Fixes were turned on or off, or the "
                "fix groups changed, since the progress was saved.)\n\n"
                "Click OK to clear the saved progress and start this file from the beginning.\n"
                "Click Cancel to abort starting so you can fix the file first.",
            ):
                return  # user wants to fix the file
            clear_resume_state(fpath)
            continue

        # Valid entry — build the resume prompt.
        pct = (chars_typed / len(clean_text) * 100) if len(clean_text) else 0
        file_name = os.path.basename(fpath)
        file_label = f"'{file_name}'" if len(files_to_run) > 1 else "this file"

        saved_at_str = entry.get("saved_at")
        date_line = ""
        if saved_at_str:
            try:
                saved_dt = datetime.fromisoformat(saved_at_str)
                date_line = f"\nSaved: {saved_dt.strftime('%b %-d, %-I:%M %p')}.\n"
            except (ValueError, AttributeError):
                try:
                    saved_dt = datetime.fromisoformat(saved_at_str)
                    date_line = f"\nSaved: {saved_dt.strftime('%b %d, %I:%M %p')}.\n"
                except Exception:
                    date_line = ""

        if messagebox.askyesno(
            "Resume Previous Session?",
            f"You stopped partway through {file_label} last time "
            f"(~{pct:.0f}% done).{date_line}\n"
            "Before clicking Yes, click into your document at the exact spot "
            "where typing stopped. Amanuensis cannot check this for you.\n\n"
            "Resume from where you left off? Choose \"No\" to start over from the beginning.",
        ):
            resume_dict[fpath] = chars_typed
        else:
            clear_resume_state(fpath)

    first_path = files_to_run[0]
    resume_from = resume_dict.get(first_path, 0)
    settings["resume_from"] = resume_from
    settings["input_path"] = first_path

    state.is_running = True
    state.chars_typed = resume_from
    state.total_chars = 0
    state.start_time = time.time()
    state.paused_duration = 0.0
    state.wpm = 0
    state.status = "Countdown..."
    state.logging_enabled = log_enabled_var.get()
    state.log_entries = []
    if auto_profile_used:
        # Only the profile's name is logged, never the window title.
        log_event("auto_profile", f"loaded profile {auto_profile_used}")
    state.left_mistakes = []
    state.left_mistakes_resumed = False
    state.locked_window_title = settings["locked_window"]
    state.settings = settings
    stop_event.clear()
    pause_event.clear()
    focus_lost_event.clear()

    btn_start.configure(state=tk.DISABLED)
    btn_preview.configure(state=tk.DISABLED)
    btn_pause.configure(state=tk.NORMAL)
    btn_stop.configure(state=tk.NORMAL)

    thread = threading.Thread(
        target=run_typing_worker,
        args=(settings, files_to_run, resume_dict),
    )
    thread.daemon = True
    thread.start()

    if settings.get("auto_pause_activity") and not settings.get("preview_mode"):
        watcher_thread = threading.Thread(
            target=_activity_watcher,
            args=(settings.get("activity_sensitivity", 20.0),),
            daemon=True,
        )
        watcher_thread.start()


def open_preview_window():
    """Opens a Toplevel window with a large Text box and runs a full
    type_text() session against it instead of the real screen. Reuses
    type_text() and the same background-thread + stop/pause
    infrastructure as a real run -- the only difference is that
    settings['preview_mode'] is True, so _send_char() / _send_backspace() /
    _send_ctrl_backspace() (see the PREVIEW MODE section above) write
    into the Text box via run_on_main_thread() instead of calling
    pyautogui. type_text() itself skips the 10-second countdown when
    preview_mode is set, since there's no target window to switch to.
    Preview runs always start from the top of the current input file
    and process just that one file -- never resume checkpoints, batch
    queues, or the session log, since none of that applies to a run
    that never touches another window (see stop_typing())."""
    global preview_text_widget

    if state.is_running:
        messagebox.showinfo("Already Running", "Stop the current session before starting a preview.")
        return

    settings = validate_settings()
    if settings is None:
        return
    settings["preview_mode"] = True
    settings["resume_from"] = 0
    settings["locked_window"] = ""

    queue_files = settings.get("file_queue", [])
    if queue_files:
        settings["input_path"] = queue_files[0]

    save_last_settings()

    preview_win = modern_toplevel(window, "Preview Run", "640x480", (400, 300))

    modern_label(
        preview_win,
        "Preview only \u2014 nothing is typed into any other window.",
        kind="muted",
    ).pack(anchor="w", padx=12, pady=(10, 4))

    preview_box = modern_textbox(preview_win)
    preview_box.configure(state=tk.DISABLED)
    preview_box.pack(fill="both", expand=True, padx=12, pady=(0, 8))

    modern_button(preview_win, "Stop Preview", stop_typing, kind="danger").pack(
        anchor="w", padx=12, pady=(0, 12)
    )

    preview_text_widget = preview_box

    def _on_preview_close():
        stop_typing()
        preview_win.destroy()

    preview_win.protocol("WM_DELETE_WINDOW", _on_preview_close)

    state.is_running = True
    state.chars_typed = 0
    state.total_chars = 0
    state.start_time = time.time()
    state.paused_duration = 0.0
    state.wpm = 0
    state.status = "Typing..."
    state.logging_enabled = False
    state.log_entries = []
    state.left_mistakes = []
    state.left_mistakes_resumed = False
    state.locked_window_title = ""
    state.settings = settings
    stop_event.clear()
    pause_event.clear()
    focus_lost_event.clear()

    btn_start.configure(state=tk.DISABLED)
    btn_preview.configure(state=tk.DISABLED)
    btn_pause.configure(state=tk.NORMAL)
    btn_stop.configure(state=tk.NORMAL)

    thread = threading.Thread(target=type_text, args=(settings,), kwargs={"is_batch": False, "is_last": True})
    thread.daemon = True
    thread.start()


SMART_FIX_REVIEW_LIMIT = 300  # most fixes listed in the review window


def open_smart_fix_review():
    """Opens a window listing every smart fix planned for the current input
    file (the first queued file when a queue is set up), with its line number
    and a before/after snippet. Nothing is typed or changed. It shows what
    WOULD change using the fix-group switches on the card, even while
    'Enable smart text fixes' itself is off."""
    settings = validate_settings()
    if settings is None:
        return

    queue_files = settings.get("file_queue", [])
    file_path = queue_files[0] if queue_files else settings["input_path"]

    if get_smart_fix_options(settings, ignore_master=True) is None:
        messagebox.showinfo("Nothing To Review", "Turn on at least one fix group first.")
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_text = f.read()
    except OSError as e:
        error_msg = str(e)
        messagebox.showerror("Error", f"Could not read input file:\n{error_msg}")
        return

    review_settings = dict(settings)
    review_settings["enable_smart_fixes"] = True
    prepared = prepare_input_text(raw_text, review_settings)
    if not prepared.fixes:
        messagebox.showinfo("Nothing To Fix", "No smart fixes are needed for this file.")
        return

    counts = {}
    for fix in prepared.fixes:
        counts[fix.kind] = counts.get(fix.kind, 0) + 1
    report_lines = ["Summary"]
    for kind, count in sorted(counts.items(), key=lambda item: -item[1]):
        report_lines.append(f"  {count} x {SMART_FIX_LABELS.get(kind, kind)}")
    report_lines.append("")

    for fix in prepared.fixes[:SMART_FIX_REVIEW_LIMIT]:
        line_number, label, before, after = describe_smart_fix(prepared.source_text, fix)
        report_lines.append(f"Line {line_number}: {label}")
        report_lines.append(f"   before: {before}")
        report_lines.append(f"   after:  {after}")
        report_lines.append("")
    hidden_count = len(prepared.fixes) - SMART_FIX_REVIEW_LIMIT
    if hidden_count > 0:
        report_lines.append(f"...and {hidden_count} more fixes not shown.")

    review_win = modern_toplevel(window, "Smart Fix Review", "720x520", (480, 320))

    intro = f"{os.path.basename(file_path)}: {len(prepared.fixes):,} fixes planned. Nothing is typed from here."
    if not settings.get("enable_smart_fixes"):
        intro += "\nSmart text fixes are switched off right now, so this is only a preview."
    intro += "\nThe changed part is shown between \u00ab \u00bb (dots are spaces, arrows are line breaks)."
    modern_label(review_win, intro, kind="muted").pack(anchor="w", padx=12, pady=(10, 4))

    review_box = modern_textbox(review_win)
    review_box.insert("1.0", "\n".join(report_lines))
    review_box.configure(state=tk.DISABLED)
    review_box.pack(fill="both", expand=True, padx=12, pady=(0, 8))

    modern_button(review_win, "Close", review_win.destroy, kind="neutral").pack(
        anchor="w", padx=12, pady=(0, 12)
    )


def build_left_mistake_report_text(mistakes, was_stopped, was_resumed):
    """Builds the plain-text list shown in (and saved from) the Mistakes Left
    For You window. One numbered entry per mistake: where it is, what kind it
    is, what is on screen, and what it should be."""
    counts = {}
    for item in mistakes:
        counts[item["label"]] = counts.get(item["label"], 0) + 1
    show_file_names = len({item["file"] for item in mistakes}) > 1

    lines = [f"{len(mistakes)} mistake(s) left in your text", ""]
    for label, count in sorted(counts.items(), key=lambda pair: -pair[1]):
        lines.append(f"  {count} x {label}")
    lines.append("")
    lines.append("The part to look at is shown between \u00ab \u00bb (\u21b5 is a line break).")
    lines.append("")

    for number, item in enumerate(mistakes, start=1):
        where = f"Line {item['line']}"
        if show_file_names:
            where = f"{item['file']}, {where}"
        lines.append(f"{number}. {where}: {item['label']}")
        lines.append(f"     on screen:  {item['typed_snippet']}")
        lines.append(f"     should be:  {item['correct_snippet']}")
        lines.append("")

    if was_stopped:
        lines.append("The run was stopped early, so only the mistakes typed so far are listed.")
    if was_resumed:
        lines.append(
            "This run was resumed, so mistakes left in an earlier session are not listed here."
        )
    return "\n".join(lines).rstrip() + "\n"


def open_left_mistake_report():
    """Opens the Mistakes Left For You window for the most recent run. It
    opens by itself when a run that left mistakes ends, and the button on the
    Leave Mistakes For Me card opens it again."""
    mistakes = list(state.left_mistakes)
    if not mistakes:
        messagebox.showinfo(
            "No Mistakes Left",
            "No mistakes have been left in your text yet.\n\n"
            "Turn on 'Leave some mistakes for me to fix' on the Mistakes tab, then start a run.",
        )
        return

    report_text = build_left_mistake_report_text(
        mistakes, state.status == "Stopped", state.left_mistakes_resumed
    )

    report_win = modern_toplevel(window, "Mistakes Left For You", "780x580", (520, 340))
    modern_label(
        report_win,
        f"{len(mistakes)} mistake(s) were purposely left in your text. "
        "Use Find (Ctrl+F) in your document to jump to each one, then fix it.",
        kind="muted",
        wraplength=720,
    ).pack(anchor="w", padx=12, pady=(10, 4))

    report_box = modern_textbox(report_win)
    report_box.insert("1.0", report_text)
    report_box.configure(state=tk.DISABLED)
    report_box.pack(fill="both", expand=True, padx=12, pady=(0, 8))

    def copy_report():
        window.clipboard_clear()
        window.clipboard_append(report_text)

    def save_report():
        save_path = filedialog.asksaveasfilename(
            parent=report_win,
            title="Save Mistake List",
            defaultextension=".txt",
            initialfile="amanuensis_mistakes.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not save_path:
            return
        try:
            with open(save_path, "w", encoding="utf-8") as report_file:
                report_file.write(report_text)
        except OSError as e:
            error_msg = str(e)
            messagebox.showerror("Error", f"Could not save the list:\n{error_msg}")

    button_row = modern_frame(report_win)
    button_row.pack(anchor="w", padx=12, pady=(0, 12))
    modern_button(button_row, "Copy List", copy_report, kind="primary").pack(side="left")
    modern_button(button_row, "Save List...", save_report, kind="neutral").pack(side="left", padx=(8, 0))
    modern_button(button_row, "Close", report_win.destroy, kind="neutral").pack(side="left", padx=(8, 0))


# --------------------------------------------
# UPDATE CHECKER (Update #25)
# --------------------------------------------
# On launch (if turned on) and when the person clicks "Check for Updates
# Now", the app asks GitHub for the newest release and compares its version
# number with APP_VERSION. It uses only the standard library, so there is no
# new package. The only thing sent is an ordinary web request, so GitHub sees
# the person's IP address and the app version. Nothing from their text or
# settings is sent.
#
# Safety rules (do not loosen these):
#   - HTTPS only, and only to the GitHub hosts in UPDATE_ALLOWED_HOSTS. A
#     redirect to any other address is refused.
#   - All network work runs on a background thread. Results reach the window
#     only through run_on_main_thread().
#   - No dialog is shown while a run is active, because a pop-up would steal
#     focus from the window being typed into.
#   - The app never replaces or restarts itself. "Download" saves the new .exe
#     as a separate file, checks it, and tells the person what to do next.
GITHUB_REPO = "jheathspooky009/Amanuensis"
UPDATE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases"
UPDATE_ASSET_NAME = "amanuensis.exe"  # the file release.yml attaches to each release
UPDATE_ALLOWED_HOSTS = (
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
)
UPDATE_TIMEOUT_SECONDS = 10
UPDATE_STARTUP_DELAY_MS = 3000       # wait a moment so the window is fully drawn first
UPDATE_MAX_API_BYTES = 1024 * 1024   # the release info should be tiny; refuse anything huge
UPDATE_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
UPDATE_NOTES_MAX_CHARS = 600

# Only touched on the main thread, so a plain flag is enough.
_update_check_running = False
_update_download_running = False


def is_allowed_update_url(url):
    """True only for an https address on one of the GitHub hosts above."""
    try:
        parts = urllib.parse.urlparse(str(url))
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and host in UPDATE_ALLOWED_HOSTS


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows redirects (GitHub sends downloads to another GitHub host) but
    refuses to be sent anywhere that is not on the allowed list."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_update_url(newurl):
            raise urllib.error.URLError("Blocked a redirect to an unexpected address.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def parse_version_text(text):
    """Turns 'v4.4.0' or '4.4' into a tuple like (4, 4, 0) so versions can be
    compared as numbers. Returns None for anything else (for example a tag
    like 'v5.0-beta'), and callers treat None as 'ignore this'."""
    match = re.fullmatch(r"[vV]?(\d+)\.(\d+)(?:\.(\d+))?", str(text).strip())
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _build_update_request(url, accept_header):
    return urllib.request.Request(
        url, headers={"Accept": accept_header, "User-Agent": f"Amanuensis/{APP_VERSION}"}
    )


def fetch_latest_release():
    """Asks GitHub for the newest published release. Runs on a background
    thread. Returns a dict. On success it has ok=True plus version,
    version_tuple, page_url, notes, asset_url, asset_size, and asset_digest
    (empty or 0 when missing). On failure it has ok=False plus a plain-English
    error message."""
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    request = _build_update_request(UPDATE_API_URL, "application/vnd.github+json")
    try:
        with opener.open(request, timeout=UPDATE_TIMEOUT_SECONDS) as response:
            raw_bytes = response.read(UPDATE_MAX_API_BYTES)
        release = json.loads(raw_bytes.decode("utf-8"))
    except urllib.error.HTTPError as http_error:
        if http_error.code == 404:
            message = (
                "GitHub has no public release for Amanuensis yet. If the repository is "
                "private, update checks cannot see it."
            )
        elif http_error.code in (403, 429):
            message = "GitHub is limiting requests right now. Try again in a little while."
        else:
            message = f"GitHub answered with error {http_error.code}."
        return {"ok": False, "error": message}
    except (urllib.error.URLError, OSError):
        return {"ok": False, "error": "Could not reach GitHub. Check your internet connection."}
    except ValueError:
        return {"ok": False, "error": "GitHub sent an answer this app could not read."}

    if not isinstance(release, dict):
        return {"ok": False, "error": "GitHub sent an answer this app could not read."}
    version_tuple = parse_version_text(release.get("tag_name", ""))
    if version_tuple is None:
        return {"ok": False, "error": "The newest release has a version name this app cannot read."}

    notes = str(release.get("body") or "").strip()
    if len(notes) > UPDATE_NOTES_MAX_CHARS:
        notes = notes[:UPDATE_NOTES_MAX_CHARS].rstrip() + "..."
    page_url = str(release.get("html_url") or "")
    if not is_allowed_update_url(page_url):
        page_url = UPDATE_RELEASES_PAGE_URL

    asset_url = ""
    asset_size = 0
    asset_digest = ""
    assets = release.get("assets")
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name", "")).lower() != UPDATE_ASSET_NAME:
            continue
        candidate_url = str(asset.get("browser_download_url") or "")
        if not is_allowed_update_url(candidate_url):
            continue
        asset_url = candidate_url
        try:
            asset_size = int(asset.get("size") or 0)
        except (TypeError, ValueError):
            asset_size = 0
        digest_match = re.fullmatch(r"sha256:([0-9a-f]{64})", str(asset.get("digest") or "").lower())
        asset_digest = digest_match.group(1) if digest_match else ""
        break

    return {
        "ok": True,
        "version": ".".join(str(part) for part in version_tuple),
        "version_tuple": version_tuple,
        "page_url": page_url,
        "notes": notes,
        "asset_url": asset_url,
        "asset_size": asset_size,
        "asset_digest": asset_digest,
    }


def _update_check_worker(is_manual):
    """Background thread body for one update check."""
    try:
        result = fetch_latest_release()
    except Exception:
        # Networking can fail in many ways. Whatever happens here, the app
        # must keep working, so report it as a normal failed check.
        result = {"ok": False, "error": "Something unexpected went wrong while checking for updates."}
    run_on_main_thread(lambda: handle_update_check_result(result, is_manual))


def run_update_check(is_manual):
    """Starts an update check in the background. is_manual is True when the
    person clicked the button (so they always get an answer) and False for
    the automatic check at startup (which stays quiet unless there is news)."""
    global _update_check_running
    if _update_check_running or _update_download_running:
        return
    if state.is_running:
        update_status_var.set("Update check skipped because a run is active.")
        return
    _update_check_running = True
    update_status_var.set("Checking for updates...")
    threading.Thread(target=_update_check_worker, args=(is_manual,), daemon=True).start()


def check_for_updates_now():
    run_update_check(True)


def run_startup_update_check():
    if enable_update_check_var.get():
        run_update_check(False)


def handle_update_check_result(result, is_manual):
    """Runs on the main thread when a check finishes."""
    global _update_check_running
    _update_check_running = False
    checked_at = datetime.now().strftime("%H:%M")

    if not result["ok"]:
        update_status_var.set(f"Last check ({checked_at}) failed: {result['error']}")
        if is_manual and not state.is_running:
            messagebox.showerror("Update Check Failed", result["error"])
        return

    current_tuple = parse_version_text(APP_VERSION)
    if current_tuple is None or result["version_tuple"] <= current_tuple:
        update_status_var.set(f"Last check ({checked_at}): you have the newest version (v{APP_VERSION}).")
        if is_manual and not state.is_running:
            messagebox.showinfo("Up To Date", f"You already have the newest version (v{APP_VERSION}).")
        return

    update_status_var.set(f"Last check ({checked_at}): version {result['version']} is available.")
    if state.is_running:
        return  # never pop up over a run; the status line above is enough
    skipped_tuple = parse_version_text(skipped_update_version_var.get())
    if not is_manual and skipped_tuple == result["version_tuple"]:
        return  # the person chose to skip this exact version
    show_update_prompt(result)


def show_update_prompt(info):
    """Tells the person a newer version exists and lets them choose what to do."""
    is_packaged = bool(getattr(sys, "frozen", False))
    can_download = is_packaged and bool(info["asset_url"])
    notes_text = info["notes"] if info["notes"] else "No release notes were provided."
    message = (
        f"Amanuensis v{info['version']} is available. You are using v{APP_VERSION}.\n\n"
        f"What's new:\n{notes_text}\n\n"
    )
    buttons = []
    if can_download:
        message += "Download saves the new version as a separate file. Nothing is replaced or restarted for you."
        buttons.append(("Download Update", "download", "primary"))
        buttons.append(("Visit GitHub Page", "visit", "neutral"))
    else:
        if is_packaged:
            message += "No downloadable file was found on that release, so use the GitHub page."
        else:
            message += "You are running Amanuensis from its source code, so update it from the GitHub page."
        buttons.append(("Visit GitHub Page", "visit", "primary"))
    buttons.append(("Skip This Version", "skip", "neutral"))
    buttons.append(("Later", "later", "neutral"))

    choice = show_modern_dialog("Update Available", message, buttons, kind="info", cancel_value="later")
    if choice == "download":
        start_update_download(info)
    elif choice == "visit":
        page_url = info["page_url"]
        if not is_allowed_update_url(page_url):
            page_url = UPDATE_RELEASES_PAGE_URL
        webbrowser.open(page_url)
    elif choice == "skip":
        skipped_update_version_var.set(info["version"])
        update_status_var.set(
            f"Skipping v{info['version']}. The startup check will stay quiet until a newer version "
            "comes out. Check for Updates Now still shows it."
        )


def choose_update_folder():
    """Picks where the downloaded file goes. Returns (folder, is_app_folder).
    Next to the running app is best, because the app keeps its saved settings
    in its own folder, so a new copy there finds them. If that folder cannot
    be written to (for example Program Files), the Downloads folder is used."""
    app_folder = get_base_dir()
    if getattr(sys, "frozen", False) and os.access(app_folder, os.W_OK):
        return app_folder, True
    downloads_folder = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.isdir(downloads_folder) and os.access(downloads_folder, os.W_OK):
        return downloads_folder, False
    return app_folder, True


def make_unique_path(folder, file_name):
    """A path in folder that does not exist yet, so nothing is overwritten."""
    base_name, extension = os.path.splitext(file_name)
    path = os.path.join(folder, file_name)
    counter = 2
    while os.path.exists(path) or os.path.exists(path + ".part"):
        path = os.path.join(folder, f"{base_name} ({counter}){extension}")
        counter += 1
    return path


def start_update_download(info):
    """Opens a small progress window and downloads the new .exe on a
    background thread."""
    global _update_download_running
    if _update_download_running or _update_check_running:
        return
    folder, is_app_folder = choose_update_folder()
    target_path = make_unique_path(folder, f"amanuensis-v{info['version']}.exe")
    cancel_event = threading.Event()
    progress_var = tk.DoubleVar(value=0.0)
    message_var = tk.StringVar(value="Starting download...")

    progress_win = modern_toplevel(window, "Downloading Update", "440x190", (400, 170))
    modern_label(progress_win, f"Downloading Amanuensis v{info['version']}", kind="status").pack(
        anchor="w", padx=14, pady=(14, 4)
    )
    modern_progressbar(progress_win, progress_var).pack(fill="x", padx=14, pady=4)
    modern_label(progress_win, textvariable=message_var, kind="muted", wraplength=400).pack(
        anchor="w", padx=14, pady=(4, 8)
    )
    modern_button(progress_win, "Cancel", cancel_event.set, kind="neutral").pack(
        anchor="e", padx=14, pady=(0, 12)
    )
    progress_win.protocol("WM_DELETE_WINDOW", cancel_event.set)

    _update_download_running = True
    update_status_var.set(f"Downloading v{info['version']}...")
    threading.Thread(
        target=_update_download_worker,
        args=(info, target_path, is_app_folder, cancel_event, progress_var, message_var, progress_win),
        daemon=True,
    ).start()


def _download_update_file(info, part_path, cancel_event, progress_var, message_var):
    """Streams the file to part_path and checks it. Returns a dict with
    ok, cancelled, verified, and text (the error message when ok is False).
    Runs on the background thread, so the window is only reached through
    run_on_main_thread()."""
    outcome = {"ok": False, "cancelled": False, "verified": False, "text": ""}
    hasher = hashlib.sha256()
    received = 0
    last_percent = -1
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    request = _build_update_request(info["asset_url"], "application/octet-stream")
    try:
        with opener.open(request, timeout=UPDATE_TIMEOUT_SECONDS) as response:
            try:
                total_bytes = int(response.headers.get("Content-Length") or info["asset_size"] or 0)
            except ValueError:
                total_bytes = int(info["asset_size"] or 0)
            if total_bytes > UPDATE_MAX_DOWNLOAD_BYTES:
                outcome["text"] = "The file is much larger than expected, so it was not downloaded."
                return outcome
            with open(part_path, "wb") as part_file:
                while True:
                    if cancel_event.is_set():
                        outcome["cancelled"] = True
                        return outcome
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    if received == 0 and not chunk.startswith(b"MZ"):
                        outcome["text"] = "The downloaded file is not a Windows program, so it was thrown away."
                        return outcome
                    received += len(chunk)
                    if received > UPDATE_MAX_DOWNLOAD_BYTES:
                        outcome["text"] = "The file is much larger than expected, so the download was stopped."
                        return outcome
                    hasher.update(chunk)
                    part_file.write(chunk)
                    if total_bytes:
                        percent = int(received * 100 / total_bytes)
                        if percent != last_percent:
                            last_percent = percent
                            megabytes = received / (1024 * 1024)
                            run_on_main_thread(lambda p=percent: progress_var.set(p))
                            run_on_main_thread(
                                lambda m=megabytes, p=percent: message_var.set(f"{p}%  ({m:.1f} MB)")
                            )
    except (urllib.error.URLError, OSError) as download_error:
        error_text = str(download_error)
        outcome["text"] = f"The download failed: {error_text}"
        return outcome

    if received == 0:
        outcome["text"] = "The download was empty."
        return outcome
    if info["asset_size"] and received != info["asset_size"]:
        outcome["text"] = "The download was incomplete (the size did not match), so it was thrown away."
        return outcome
    if info["asset_digest"]:
        if hasher.hexdigest() != info["asset_digest"]:
            outcome["text"] = (
                "The downloaded file did not match the checksum GitHub published for it, "
                "so it was thrown away. Do not use it."
            )
            return outcome
        outcome["verified"] = True
    outcome["ok"] = True
    return outcome


def _update_download_worker(info, target_path, is_app_folder, cancel_event, progress_var, message_var, progress_win):
    """Background thread body for one download."""
    part_path = target_path + ".part"
    try:
        outcome = _download_update_file(info, part_path, cancel_event, progress_var, message_var)
        if outcome["ok"]:
            try:
                os.replace(part_path, target_path)
            except OSError as move_error:
                move_text = str(move_error)
                outcome = {"ok": False, "cancelled": False, "verified": False,
                           "text": f"The file could not be saved: {move_text}"}
    except Exception:
        outcome = {"ok": False, "cancelled": False, "verified": False,
                   "text": "Something unexpected went wrong during the download."}
    finally:
        # Anything still named .part is an unfinished or rejected download.
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass
    run_on_main_thread(
        lambda: finish_update_download(progress_win, info, target_path, is_app_folder, outcome)
    )


def finish_update_download(progress_win, info, target_path, is_app_folder, outcome):
    """Runs on the main thread when a download ends, whatever the result."""
    global _update_download_running
    _update_download_running = False
    try:
        progress_win.destroy()
    except tk.TclError:
        pass

    if outcome["cancelled"]:
        update_status_var.set("Download cancelled.")
        return
    if not outcome["ok"]:
        update_status_var.set("Download failed.")
        if not state.is_running:
            messagebox.showerror("Download Failed", outcome["text"])
        return

    update_status_var.set(f"Downloaded v{info['version']} to: {target_path}")
    if state.is_running:
        return  # no pop-up over a run; the status line above has the location
    if outcome["verified"]:
        check_text = "Its checksum matched the one GitHub published."
    else:
        check_text = "GitHub did not publish a checksum for this file, so it could not be verified."
    if is_app_folder:
        settings_text = "It sits next to your current copy, so your saved settings will carry over."
    else:
        settings_text = (
            "It is not next to your current copy. To keep your settings, copy last_settings.json "
            "from the old app's folder into the new file's folder."
        )
    open_folder = messagebox.askyesno(
        "Update Downloaded",
        f"Amanuensis v{info['version']} was saved to:\n{target_path}\n\n"
        f"{check_text}\n{settings_text}\n\n"
        "To finish updating, close Amanuensis and open the new file. Open that folder now?",
    )
    if open_folder and hasattr(os, "startfile"):
        try:
            os.startfile(os.path.dirname(target_path))
        except OSError:
            pass


def toggle_pause():
    if not state.is_running:
        return
    if pause_event.is_set():
        pause_event.clear()
        btn_pause.configure(text="Pause Typing")
    else:
        pause_event.set()
        btn_pause.configure(text="Resume Typing")
        log_event("manual_pause", "")


def stop_typing():
    global preview_text_widget
    was_running = state.is_running
    was_preview = state.settings.get("preview_mode", False)
    state.is_running = False
    stop_event.set()
    pause_event.clear()
    focus_lost_event.clear()

    btn_start.configure(state=tk.NORMAL)
    btn_preview.configure(state=tk.NORMAL)
    btn_pause.configure(state=tk.DISABLED, text="Pause Typing")
    btn_stop.configure(state=tk.DISABLED)

    if state.status != "Finished!":
        state.status = "Stopped"
        if was_running and not was_preview:
            log_event("session_end", "stopped")
            input_path = state.settings.get("input_path", "")
            if input_path and 0 < state.chars_typed < state.total_chars:
                clean_text = state.clean_text
                prefix_hash = hash_clean_prefix(clean_text, state.chars_typed) if clean_text else None
                save_resume_state(input_path, state.chars_typed, state.total_chars, prefix_hash)
    else:
        if not was_preview:
            input_path = state.settings.get("input_path", "")
            if input_path:
                clear_resume_state(input_path)
        notify_finished()

    if state.logging_enabled and state.log_entries and not was_preview:
        export_log(auto=True)

    if was_preview:
        preview_text_widget = None

    # Tell the person which mistakes were purposely left for them (Update #24).
    if was_running and state.left_mistakes:
        open_left_mistake_report()

    run_on_main_thread(refresh_resume_status)


# --------------------------------------------
# WINDOW SELECTION (target window lock)
# --------------------------------------------
def refresh_window_list():
    if not HAS_PYGETWINDOW:
        return
    titles = [t for t in gw.getAllTitles() if t.strip()]
    seen = set()
    unique_titles = []
    for t in titles:
        if t not in seen:
            seen.add(t)
            unique_titles.append(t)
    window_combo.configure(values=unique_titles or [""])
    if unique_titles and not window_var.get():
        window_var.set(unique_titles[0])


# --------------------------------------------
# HOTKEY BINDING (debounced -- applied explicitly, not per keystroke)
# --------------------------------------------
def apply_hotkeys(event=None):
    keyboard.unhook_all()
    warnings = []
    bindings = [
        (hotkey_start_var.get().strip(), start_typing, "Start"),
        (hotkey_pause_var.get().strip(), toggle_pause, "Pause"),
        (hotkey_stop_var.get().strip(), stop_typing, "Stop"),
    ]
    for hotkey, func, label in bindings:
        if not hotkey:
            continue
        try:
            # The `keyboard` library fires this callback on its own
            # background listener thread, not the main thread -- so
            # it must only ever enqueue the action, never call the
            # GUI function directly.
            keyboard.add_hotkey(hotkey, lambda f=func: run_on_main_thread(f))
        except Exception:
            warnings.append(f"{label} ('{hotkey}')")
    hotkey_warning_var.set("\u26a0 Invalid hotkey(s): " + ", ".join(warnings) if warnings else "")


# --------------------------------------------
# TYPING PROFILES (save / load settings as JSON)
# --------------------------------------------
def get_base_dir():
    """Resolve the app's own directory whether running as a plain
    .py script or as a PyInstaller-frozen .exe (this project ships
    an amanuensis.spec / dist build, so both cases matter)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


PROFILE_DIR = os.path.join(get_base_dir(), "profiles")
LAST_SETTINGS_PATH = os.path.join(get_base_dir(), "last_settings.json")
RESUME_STATE_PATH = os.path.join(get_base_dir(), "resume_state.json")


def get_current_settings_snapshot():
    return {
        "min_delay": min_delay_var.get(),
        "max_delay": max_delay_var.get(),
        "hotkey_start": hotkey_start_var.get(),
        "hotkey_pause": hotkey_pause_var.get(),
        "hotkey_stop": hotkey_stop_var.get(),
        "mistake_min_words": mistake_min_var.get(),
        "mistake_max_words": mistake_max_var.get(),
        "break_min_paragraphs": break_min_var.get(),
        "break_max_paragraphs": break_max_var.get(),
        "break_duration_min_seconds": break_duration_min_var.get(),
        "break_duration_max_seconds": break_duration_max_var.get(),
        "enable_mistakes": enable_mistakes_var.get(),
        "enable_breaks": enable_breaks_var.get(),
        "log_enabled": log_enabled_var.get(),
        "enable_typos": enable_typos_var.get(),
        "typo_chance": typo_chance_var.get(),
        "use_extra_typos": use_extra_typos_var.get(),
        "enable_paraphrase": enable_paraphrase_var.get(),
        "paraphrase_chance": paraphrase_chance_var.get(),
        "paragraph_pause_min": paragraph_pause_min_var.get(),
        "paragraph_pause_max": paragraph_pause_max_var.get(),
        "sentence_pause_min": sentence_pause_min_var.get(),
        "sentence_pause_max": sentence_pause_max_var.get(),
        "input_path": input_path_var.get(),
        "file_queue": get_queue_files(),
        "enable_auto_profile": enable_auto_profile_var.get(),
        "auto_profile_rules": get_auto_profile_rules(),
        "enable_notify": enable_notify_var.get(),
        "enable_sound": enable_sound_var.get(),
        "auto_pause_activity": auto_pause_activity_var.get(),
        "activity_sensitivity": activity_sensitivity_var.get(),
        "enable_fatigue": enable_fatigue_var.get(),
        "fatigue_strength": fatigue_strength_var.get(),
        "enable_finger_timing": enable_finger_timing_var.get(),
        "enable_typing_moods": enable_typing_moods_var.get(),
        "typing_mood_strength": typing_mood_strength_var.get(),
        "enable_difficulty_pauses": enable_difficulty_pauses_var.get(),
        "difficulty_pause_strength": difficulty_pause_strength_var.get(),
        "enable_target_time": enable_target_time_var.get(),
        "target_time_hours": target_hours_var.get(),
        "target_time_minutes": target_minutes_var.get(),
        "enable_pause_markers": enable_pause_markers_var.get(),
        "enable_format_markers": enable_format_markers_var.get(),
        "enable_smart_fixes": enable_smart_fixes_var.get(),
        "smart_fix_spacing": smart_fix_spacing_var.get(),
        "smart_fix_punctuation": smart_fix_punctuation_var.get(),
        "smart_fix_capitalization": smart_fix_capitalization_var.get(),
        "smart_fix_repeated_words": smart_fix_repeated_words_var.get(),
        "smart_fix_visible_chance": smart_fix_visible_chance_var.get(),
        "enable_leave_mistakes": enable_leave_mistakes_var.get(),
        "leave_mistake_count": leave_mistake_count_var.get(),
        "leave_mistakes_typos": leave_mistakes_typos_var.get(),
        "leave_mistakes_commas": leave_mistakes_commas_var.get(),
        "leave_mistakes_punctuation": leave_mistakes_punctuation_var.get(),
        "enable_update_check": enable_update_check_var.get(),
        "skipped_update_version": skipped_update_version_var.get(),
        "theme": theme_var.get(),
    }


def apply_settings_snapshot(data, include_files=True, rebind_hotkeys=True):
    """Puts saved settings into the window. include_files=False ignores the
    personal parts of the data (the batch queue and the Auto Profile rules),
    which is what loading a shared profile does. rebind_hotkeys=False leaves
    the hotkey bindings alone (used when a profile is auto-picked at the
    moment Start is pressed). Malformed or non-dictionary data is ignored so a
    damaged profile or hand-edited settings file cannot crash the app."""
    if not isinstance(data, dict):
        return

    min_delay_var.set(data.get("min_delay", "0.05"))
    max_delay_var.set(data.get("max_delay", "0.15"))
    hotkey_start_var.set(data.get("hotkey_start", "f8"))
    hotkey_pause_var.set(data.get("hotkey_pause", "f9"))
    hotkey_stop_var.set(data.get("hotkey_stop", "esc"))
    mistake_min_var.set(data.get("mistake_min_words", "100"))
    mistake_max_var.set(data.get("mistake_max_words", "200"))
    break_min_var.set(data.get("break_min_paragraphs", "3"))
    break_max_var.set(data.get("break_max_paragraphs", "6"))
    break_duration_min_var.set(data.get("break_duration_min_seconds", "60"))
    break_duration_max_var.set(data.get("break_duration_max_seconds", "180"))
    enable_mistakes_var.set(data.get("enable_mistakes", True))
    enable_breaks_var.set(data.get("enable_breaks", True))
    log_enabled_var.set(data.get("log_enabled", False))
    enable_typos_var.set(data.get("enable_typos", True))
    typo_chance_var.set(data.get("typo_chance", "4"))
    use_extra_typos_var.set(data.get("use_extra_typos", False))
    enable_paraphrase_var.set(data.get("enable_paraphrase", True))
    paraphrase_chance_var.set(data.get("paraphrase_chance", "15"))
    paragraph_pause_min_var.set(data.get("paragraph_pause_min", "10"))
    paragraph_pause_max_var.set(data.get("paragraph_pause_max", "20"))
    sentence_pause_min_var.set(data.get("sentence_pause_min", "5"))
    sentence_pause_max_var.set(data.get("sentence_pause_max", "7"))
    enable_notify_var.set(data.get("enable_notify", True))
    enable_sound_var.set(data.get("enable_sound", True))
    auto_pause_activity_var.set(data.get("auto_pause_activity", False))
    activity_sensitivity_var.set(str(data.get("activity_sensitivity", "20")))
    enable_fatigue_var.set(data.get("enable_fatigue", False))
    fatigue_strength_var.set(str(data.get("fatigue_strength", "15")))
    enable_finger_timing_var.set(bool(data.get("enable_finger_timing", True)))
    enable_typing_moods_var.set(bool(data.get("enable_typing_moods", True)))
    typing_mood_strength_var.set(str(data.get("typing_mood_strength", "100")))
    enable_difficulty_pauses_var.set(bool(data.get("enable_difficulty_pauses", True)))
    difficulty_pause_strength_var.set(str(data.get("difficulty_pause_strength", "100")))
    enable_target_time_var.set(bool(data.get("enable_target_time", False)))
    target_hours_var.set(str(data.get("target_time_hours", "2")))
    target_minutes_var.set(str(data.get("target_time_minutes", "0")))
    enable_pause_markers_var.set(data.get("enable_pause_markers", True))
    enable_format_markers_var.set(bool(data.get("enable_format_markers", True)))
    enable_smart_fixes_var.set(data.get("enable_smart_fixes", False))
    smart_fix_spacing_var.set(data.get("smart_fix_spacing", True))
    smart_fix_punctuation_var.set(data.get("smart_fix_punctuation", True))
    smart_fix_capitalization_var.set(data.get("smart_fix_capitalization", True))
    smart_fix_repeated_words_var.set(data.get("smart_fix_repeated_words", True))
    smart_fix_visible_chance_var.set(str(data.get("smart_fix_visible_chance", "50")))
    enable_leave_mistakes_var.set(data.get("enable_leave_mistakes", False))
    leave_mistake_count_var.set(str(data.get("leave_mistake_count", "3")))
    leave_mistakes_typos_var.set(data.get("leave_mistakes_typos", True))
    leave_mistakes_commas_var.set(data.get("leave_mistakes_commas", True))
    leave_mistakes_punctuation_var.set(data.get("leave_mistakes_punctuation", True))
    enable_update_check_var.set(bool(data.get("enable_update_check", True)))
    skipped_update_version_var.set(str(data.get("skipped_update_version", "")))
    saved_theme = data.get("theme", "light")
    if isinstance(saved_theme, str) and saved_theme in ("light", "dark"):
        theme_var.set(saved_theme)
        if "btn_theme_toggle" in globals():
            btn_theme_toggle.configure(text=f"Theme: {saved_theme.capitalize()}")
    if include_files and "file_queue" in data and isinstance(data["file_queue"], (list, tuple)):
        file_queue_var.set(list(data["file_queue"]))
    if include_files:
        enable_auto_profile_var.set(bool(data.get("enable_auto_profile", False)))
        set_auto_profile_rules(data.get("auto_profile_rules", []))
    if rebind_hotkeys:
        apply_hotkeys()


def save_profile():
    os.makedirs(PROFILE_DIR, exist_ok=True)
    path = filedialog.asksaveasfilename(
        title="Save Profile",
        initialdir=PROFILE_DIR,
        defaultextension=".json",
        filetypes=[("JSON files", "*.json")],
    )
    if not path:
        return
    try:
        # Profiles are made to be shared, so they never carry folder paths or
        # the Auto Profile rules (which hold window title words).
        profile_data = get_current_settings_snapshot()
        profile_data.pop("input_path", None)
        profile_data.pop("file_queue", None)
        profile_data.pop("enable_auto_profile", None)
        profile_data.pop("auto_profile_rules", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, indent=2)
        messagebox.showinfo("Profile Saved", f"Profile saved to:\n{path}")
    except OSError as e:
        messagebox.showerror("Save Failed", str(e))


def load_profile():
    os.makedirs(PROFILE_DIR, exist_ok=True)
    path = filedialog.askopenfilename(
        title="Load Profile",
        initialdir=PROFILE_DIR,
        filetypes=[("JSON files", "*.json")],
    )
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            messagebox.showerror("Load Failed", "That file is not an Amanuensis profile.")
            return
        apply_settings_snapshot(data, include_files=False)
        messagebox.showinfo("Profile Loaded", f"Profile loaded from:\n{path}")
    except (OSError, ValueError, TypeError, AttributeError, tk.TclError) as e:
        messagebox.showerror("Load Failed", str(e))


# --------------------------------------------
# AUTO PROFILE BY WINDOW (Update #30)
# --------------------------------------------
# A rule says: if the target window's title contains these words, load this
# saved profile. The rules live as JSON text in one tk variable so they fit the
# normal settings pattern. They hold window title words, which can be personal,
# so they are never saved into a profile and are ignored when one is loaded.
MAX_AUTO_PROFILE_RULES = 20
MAX_AUTO_PROFILE_KEYWORD_LENGTH = 60
MAX_PROFILE_FILE_BYTES = 1000000

# An auto-picked profile never changes these. They belong to the app or to the
# current run, not to a typing style.
AUTO_PROFILE_KEEP_KEYS = (
    "hotkey_start",
    "hotkey_pause",
    "hotkey_stop",
    "theme",
    "enable_update_check",
    "skipped_update_version",
    "input_path",
    "file_queue",
    "enable_auto_profile",
    "auto_profile_rules",
)


def is_safe_profile_name(name):
    """True only for a plain '.json' file name with no folder part, so a rule
    can only ever point at a file inside the profiles folder."""
    if not isinstance(name, str) or not name:
        return False
    if name != os.path.basename(name) or name in (".", ".."):
        return False
    if any(ch in name for ch in ("/", "\\", ":", "\x00")):
        return False
    return name.lower().endswith(".json") and len(name) > len(".json")


def clean_auto_profile_rules(value):
    """Turns saved data (a list, or JSON text holding a list) into a clean list
    of {"keyword": ..., "profile": ...} rules. Anything malformed is skipped,
    because settings files are untrusted input."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return []
    if not isinstance(value, (list, tuple)):
        return []
    rules = []
    for item in value:
        if not isinstance(item, dict):
            continue
        keyword = item.get("keyword")
        profile_name = item.get("profile")
        if not isinstance(keyword, str) or not isinstance(profile_name, str):
            continue
        keyword = keyword.strip()
        if not keyword or len(keyword) > MAX_AUTO_PROFILE_KEYWORD_LENGTH:
            continue
        if not is_safe_profile_name(profile_name):
            continue
        rules.append({"keyword": keyword, "profile": profile_name})
        if len(rules) >= MAX_AUTO_PROFILE_RULES:
            break
    return rules


def get_auto_profile_rules():
    """The current rules as a clean list of dictionaries."""
    return clean_auto_profile_rules(auto_profile_rules_var.get())


def set_auto_profile_rules(rules):
    """Stores rules (cleaned first). Changing the variable also refreshes the
    list shown on the Window tab."""
    auto_profile_rules_var.set(json.dumps(clean_auto_profile_rules(rules)))


def list_profile_files():
    """Names of the saved profile files inside the profiles folder."""
    try:
        names = os.listdir(PROFILE_DIR)
    except OSError:
        return []
    good_names = [
        name for name in names
        if is_safe_profile_name(name) and os.path.isfile(os.path.join(PROFILE_DIR, name))
    ]
    return sorted(good_names, key=str.lower)


def refresh_auto_profile_choices():
    """Refills the profile dropdown from the profiles folder."""
    names = list_profile_files()
    auto_profile_combo.configure(values=names or [""])
    if auto_profile_choice_var.get() not in names:
        auto_profile_choice_var.set(names[0] if names else "")


def refresh_auto_profile_listbox(*args):
    """Redraws the rule list on the Window tab from the saved rules."""
    if "auto_profile_listbox" not in globals():
        return
    auto_profile_listbox.delete(0, tk.END)
    for rule in get_auto_profile_rules():
        auto_profile_listbox.insert(tk.END, f'Title has "{rule["keyword"]}"  ->  {rule["profile"]}')


def add_auto_profile_rule():
    """Adds the typed words and the chosen profile as a new rule."""
    if state.is_running:
        return
    keyword = auto_profile_keyword_var.get().strip()
    profile_name = auto_profile_choice_var.get().strip()
    if not keyword:
        messagebox.showinfo("Add Rule", "Type some words from the window's title first, like Google Docs.")
        return
    if len(keyword) > MAX_AUTO_PROFILE_KEYWORD_LENGTH:
        messagebox.showinfo(
            "Add Rule", f"Please use {MAX_AUTO_PROFILE_KEYWORD_LENGTH} characters or fewer for the title words."
        )
        return
    if not profile_name:
        messagebox.showinfo(
            "Add Rule",
            "Pick a saved profile first. Save one with 'Save Profile...' on the Data tab, "
            "keep it in the profiles folder, then click Refresh.",
        )
        return
    rules = get_auto_profile_rules()
    if len(rules) >= MAX_AUTO_PROFILE_RULES:
        messagebox.showinfo("Add Rule", f"You can have up to {MAX_AUTO_PROFILE_RULES} rules. Remove one first.")
        return
    rules.append({"keyword": keyword, "profile": profile_name})
    set_auto_profile_rules(rules)
    auto_profile_keyword_var.set("")


def remove_selected_auto_profile_rules():
    """Removes the highlighted rules from the list."""
    if state.is_running:
        return
    chosen = set(auto_profile_listbox.curselection())
    if not chosen:
        return
    rules = get_auto_profile_rules()
    set_auto_profile_rules([rule for index, rule in enumerate(rules) if index not in chosen])


def get_target_window_title():
    """The title of the window the person is typing into: the locked window when
    Lock typing is on, otherwise the window that is active right now. Returns an
    empty string when it cannot be known (including when the active window is
    Amanuensis itself, which happens when the Start button was clicked)."""
    if not HAS_PYGETWINDOW:
        return ""
    if lock_window_var.get():
        return window_var.get().strip()
    try:
        active = gw.getActiveWindow()
        title = active.title if active else ""
    except Exception:
        return ""
    if not isinstance(title, str) or title == window.title():
        return ""
    return title.strip()


def find_auto_profile_rule(title, rules):
    """The first rule whose words appear in the title (capitals ignored), or None."""
    lowered_title = title.lower()
    for rule in rules:
        if rule["keyword"].lower() in lowered_title:
            return rule
    return None


def read_auto_profile_file(profile_name):
    """Reads a profile from the profiles folder. Returns its data as a
    dictionary, or None if it is missing, too big, or not a profile."""
    if not is_safe_profile_name(profile_name):
        return None
    path = os.path.join(PROFILE_DIR, profile_name)
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > MAX_PROFILE_FILE_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def load_auto_profile(profile_name):
    """Puts a saved profile into the settings. Hotkeys, theme, update settings,
    files, and the Auto Profile rules stay as they are. Returns True on success.
    If anything goes wrong part way, the earlier settings are put back."""
    data = read_auto_profile_file(profile_name)
    if data is None:
        return False
    backup = get_current_settings_snapshot()
    merged = dict(backup)
    for key, value in data.items():
        if key in backup and key not in AUTO_PROFILE_KEEP_KEYS:
            merged[key] = value
    try:
        apply_settings_snapshot(merged, include_files=False, rebind_hotkeys=False)
    except (ValueError, TypeError, AttributeError, tk.TclError):
        apply_settings_snapshot(backup, include_files=False, rebind_hotkeys=False)
        return False
    return True


def apply_auto_profile_for_start():
    """Runs when Start is pressed, before the settings are checked. Returns
    (keep_starting, profile_name). keep_starting is False only when the person
    cancels because a matching profile could not be loaded. profile_name is
    the profile that was loaded, or an empty string if none was."""
    auto_profile_status_var.set("")
    if not enable_auto_profile_var.get():
        return True, ""
    if not HAS_PYGETWINDOW:
        auto_profile_status_var.set("Auto pick needs pygetwindow, which is not installed.")
        return True, ""
    rules = get_auto_profile_rules()
    if not rules:
        auto_profile_status_var.set("Auto pick is on, but there are no rules yet.")
        return True, ""
    title = get_target_window_title()
    if not title:
        auto_profile_status_var.set(
            "Auto pick could not tell which window you are typing into, so your current settings were used. "
            "Use the Start hotkey, or lock a window."
        )
        return True, ""
    rule = find_auto_profile_rule(title, rules)
    if rule is None:
        auto_profile_status_var.set("No rule matched the target window, so your current settings were used.")
        return True, ""

    profile_name = rule["profile"]
    if load_auto_profile(profile_name):
        auto_profile_status_var.set(f'Last auto pick: loaded "{profile_name}" (matched "{rule["keyword"]}").')
        return True, profile_name
    if messagebox.askokcancel(
        "Auto Profile Problem",
        f"The profile '{profile_name}' could not be loaded. It may be missing, damaged, or not a profile.\n\n"
        "Click OK to type with your current settings instead.\n"
        "Click Cancel to stop so you can fix the rule.",
    ):
        auto_profile_status_var.set(f'Could not load "{profile_name}", so your current settings were used.')
        return True, ""
    return False, ""


def _reset_is_blocked():
    """Resetting settings mid-run would be confusing (the run already
    captured its settings), so every reset is refused while a run or
    preview is active. Returns True if the caller should stop."""
    if state.is_running:
        messagebox.showinfo(
            "Reset Unavailable",
            "Stop the current run before resetting settings.",
        )
        return True
    return False


def _apply_section_defaults(section_name):
    """Puts every variable in one section back to the default value that
    was captured at startup (see SECTION_DEFAULTS). Does no confirmation
    and no running-check; callers handle both."""
    for var, default_value in SECTION_DEFAULTS[section_name]:
        var.set(default_value)
    if section_name == "Hotkeys":
        # Hotkey changes only take effect once they are re-registered.
        apply_hotkeys()


def reset_section(section_name):
    """Reset ONE settings card back to its defaults, after asking the
    user to confirm. Other cards are left exactly as they are."""
    if _reset_is_blocked():
        return
    if not messagebox.askyesno(
        f"Reset {section_name}",
        f"Reset the '{section_name}' settings to their default values?\n\n"
        "Only this section will change.",
    ):
        return
    _apply_section_defaults(section_name)


def reset_to_defaults():
    """Reset EVERY settings card back to its defaults, after asking
    for confirmation. Uses the same section list as the per-card
    buttons. The theme, Input File, batch queue, and Target Window
    Lock are not settings cards and are left alone."""
    if _reset_is_blocked():
        return
    if not messagebox.askyesno(
        "Reset All Settings",
        "Reset all settings to their default values?\n\n"
        "This will overwrite whatever you currently have entered.\n"
        "Your theme, input file, and file queue will not change.",
    ):
        return
    for section_name in SECTION_DEFAULTS:
        _apply_section_defaults(section_name)


def save_last_settings():
    """Best-effort auto-save of the current settings so they're
    restored automatically next launch, with no need to use Save
    Profile every time. Silently does nothing on failure -- this
    should never block closing the app or starting a session."""
    try:
        with open(LAST_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(get_current_settings_snapshot(), f, indent=2)
    except OSError:
        pass


def load_last_settings():
    """Best-effort auto-restore of the last-used settings on launch.
    Silently falls back to the built-in defaults if no saved
    settings exist yet or the file can't be read."""
    try:
        with open(LAST_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return  # not a settings file, so keep the built-in defaults
        apply_settings_snapshot(data)
        saved_input_path = data.get("input_path")
        if isinstance(saved_input_path, str) and saved_input_path:
            input_path_var.set(saved_input_path)
    except (OSError, ValueError, TypeError, AttributeError, tk.TclError):
        pass


def on_close():
    stop_typing()
    save_last_settings()
    window.destroy()


def make_resume_key(input_path):
    """Return the canonical dict key for a file path: lowercase, absolute."""
    return os.path.normcase(os.path.abspath(input_path))


def hash_clean_prefix(clean_text, chars_typed):
    """SHA-256 of clean_text[:chars_typed] encoded as UTF-8."""
    prefix = clean_text[:chars_typed].encode("utf-8")
    return hashlib.sha256(prefix).hexdigest()


def read_resume_store():
    """Read resume_state.json and return a v2 entries dict.
    Migrates old v1 format ({input_path, chars_typed} at top level) on the fly.
    Returns {} on any error or missing file -- never raises."""
    try:
        with open(RESUME_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    # v2 format
    if isinstance(data, dict) and data.get("version") == 2:
        entries = data.get("entries", {})
        return entries if isinstance(entries, dict) else {}

    # v1 migration: single-file {input_path, chars_typed} at top level
    if isinstance(data, dict) and "input_path" in data and "chars_typed" in data:
        old_path = data.get("input_path", "")
        old_chars = data.get("chars_typed", 0)
        if old_path and isinstance(old_chars, int) and old_chars > 0:
            key = make_resume_key(old_path)
            migrated_entry = {
                "input_path": old_path,
                "chars_typed": old_chars,
                "total_chars": None,
                "prefix_hash": None,  # unknown; skip validation
                "saved_at": None,
            }
            return {key: migrated_entry}

    return {}


def write_resume_store(entries):
    """Atomically write the entries dict to resume_state.json.
    Uses write-to-tmp then os.replace so a crash mid-save cannot corrupt the file.
    Holds _resume_store_lock; callers may also hold it themselves."""
    tmp_path = RESUME_STATE_PATH + ".tmp"
    try:
        data = {"version": 2, "entries": entries}
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, RESUME_STATE_PATH)
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def get_clean_text_for_file(path, settings):
    """Read `path`, prepare it the same way type_text() does (markers
    removed, smart fixes applied when on), and return the final text that
    gets typed. Resume checks need this to match exactly. Returns None on OSError."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    return prepare_input_text(raw, settings).clean_text


def load_resume_entry(path):
    """Return the saved entry dict for `path`, or None if not found."""
    key = make_resume_key(path)
    with _resume_store_lock:
        entries = read_resume_store()
    return entries.get(key)


def validate_resume_entry(entry, clean_text):
    """Return True if the entry is valid against clean_text.

    An entry is valid when:
      - chars_typed > 0 and chars_typed < len(clean_text)  (not at end)
      - prefix_hash is None  (migrated old entry; skip hash check)
      - OR prefix_hash matches SHA-256 of clean_text[:chars_typed]
    """
    chars_typed = entry.get("chars_typed", 0)
    if not (0 < chars_typed < len(clean_text)):
        return False
    prefix_hash = entry.get("prefix_hash")
    if prefix_hash is None:
        return True  # migrated entry; no hash to check
    expected = hash_clean_prefix(clean_text, chars_typed)
    return prefix_hash == expected


def save_resume_state(input_path, chars_typed, total_chars, prefix_hash):
    """Persist how far into `input_path` typing got, with a hash of
    the typed prefix for change detection on next resume."""
    key = make_resume_key(input_path)
    entry = {
        "input_path": input_path,
        "chars_typed": chars_typed,
        "total_chars": total_chars,
        "prefix_hash": prefix_hash,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _resume_store_lock:
        entries = read_resume_store()
        entries[key] = entry
        write_resume_store(entries)


def clear_resume_state(input_path):
    """Remove the saved-progress entry for `input_path` only.
    Does nothing if there is no entry for that file."""
    key = make_resume_key(input_path)
    with _resume_store_lock:
        entries = read_resume_store()
        if key in entries:
            del entries[key]
            write_resume_store(entries)


def load_resume_state_for(input_path):
    """Thin wrapper used by preview_estimate(). Returns chars_typed or None."""
    entry = load_resume_entry(input_path)
    if entry is None:
        return None
    chars_typed = entry.get("chars_typed", 0)
    return chars_typed if chars_typed > 0 else None


def refresh_resume_status():
    """Update the resume_status_var label in the Input File card.
    Always called on the main thread. Reads the stored entries only --
    no file I/O -- so it is fast and safe to call after every relevant change."""
    queue_files = get_queue_files()

    if queue_files:
        # Queue mode: count how many queued files have saved progress.
        count_with_progress = 0
        for fpath in queue_files:
            entry = load_resume_entry(fpath)
            if entry and entry.get("chars_typed", 0) > 0:
                count_with_progress += 1
        total = len(queue_files)
        if count_with_progress == 0:
            resume_status_var.set(f"No saved progress for any of the {total} queued files.")
        else:
            resume_status_var.set(
                f"Saved progress found for {count_with_progress} of {total} queued files."
            )
        return

    # Single-file mode.
    input_path = input_path_var.get().strip()
    if not input_path:
        resume_status_var.set("")
        return

    entry = load_resume_entry(input_path)
    if entry is None or entry.get("chars_typed", 0) <= 0:
        file_name = os.path.basename(input_path)
        resume_status_var.set(f"No saved progress for '{file_name}'.")
        return

    chars_typed = entry["chars_typed"]
    total_chars = entry.get("total_chars") or 0
    file_name = os.path.basename(input_path)

    pct_str = ""
    if total_chars > 0:
        pct = chars_typed / total_chars * 100
        pct_str = f"{pct:.0f}% of "

    date_str = ""
    saved_at_str = entry.get("saved_at")
    if saved_at_str:
        try:
            saved_dt = datetime.fromisoformat(saved_at_str)
            time_part = saved_dt.strftime("%I:%M %p").lstrip("0")
            date_str = f" (saved {saved_dt.strftime('%b')} {saved_dt.day}, {time_part})"
        except Exception:
            date_str = ""

    resume_status_var.set(f"Saved progress: {pct_str}'{file_name}'{date_str}.")


def clear_saved_progress():
    """Clear resume progress entries for the current queue or single input file,
    after asking the user for confirmation. Does nothing while typing is running."""
    if state.is_running:
        return

    queue_files = get_queue_files()
    if queue_files:
        if not messagebox.askyesno(
            "Clear Saved Progress?",
            f"Clear saved progress for all {len(queue_files)} queued files?",
        ):
            return
        for fpath in queue_files:
            clear_resume_state(fpath)
    else:
        input_path = input_path_var.get().strip()
        if not input_path:
            return
        file_name = os.path.basename(input_path)
        if not messagebox.askyesno(
            "Clear Saved Progress?",
            f"Clear saved progress for '{file_name}'?",
        ):
            return
        clear_resume_state(input_path)

    refresh_resume_status()


# --------------------------------------------
# FILE PICKER & BATCH QUEUE
# --------------------------------------------
def browse_input_file():
    if state.is_running:
        return
    path = filedialog.askopenfilename(
        title="Select Input Text File",
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
    )
    if path:
        input_path_var.set(path)


def use_pasted_text():
    """Write the contents of the paste Text box to a scratch file and
    point input_path_var at it.  Every file-based feature (protected-text
    markers, resume checkpoints, Preview Time Estimate) continues to work
    unchanged because they all simply read whatever path input_path_var
    holds."""
    content = paste_text_box.get("1.0", tk.END).rstrip("\n")
    if not content.strip():
        messagebox.showerror("Empty Input", "Please paste or type some text before clicking \"Use Pasted Text\".")
        return
    scratch_path = os.path.join(get_base_dir(), "_pasted_input.txt")
    try:
        with open(scratch_path, "w", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        err_msg = str(exc)
        messagebox.showerror("Write Error", f"Could not save pasted text:\n{err_msg}")
        return
    input_path_var.set(scratch_path)


def get_queue_files():
    """Return a list of file paths currently in the batch queue."""
    if "file_queue_listbox" in globals():
        return list(file_queue_listbox.get(0, tk.END))
    try:
        val = file_queue_var.get()
        if isinstance(val, (list, tuple)):
            return list(val)
    except (NameError, AttributeError, tk.TclError):
        pass
    return []


def add_queue_files():
    """Open multi-select file dialog and append chosen .txt files to the queue."""
    if state.is_running:
        return
    paths = filedialog.askopenfilenames(
        title="Add Text Files to Queue",
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
    )
    if paths:
        for p in paths:
            file_queue_listbox.insert(tk.END, p)
        file_queue_var.set(list(file_queue_listbox.get(0, tk.END)))


def remove_selected_queue_files():
    """Remove highlighted items from the batch queue."""
    if state.is_running:
        return
    selected_indices = list(file_queue_listbox.curselection())
    for idx in reversed(selected_indices):
        file_queue_listbox.delete(idx)
    file_queue_var.set(list(file_queue_listbox.get(0, tk.END)))


def clear_queue_files():
    """Remove all items from the batch queue."""
    if state.is_running:
        return
    file_queue_listbox.delete(0, tk.END)
    file_queue_var.set([])


def apply_target_wpm(event=None):
    """Convert a target words-per-minute into a Min/Max Delay range
    and write it directly into those fields, using the same
    5-characters-per-word convention the live WPM stat and time
    estimate already use. This sets raw per-character typing speed
    only -- pauses, breaks, reworks, and typos are separate and will
    still bring the overall session average below this number, same
    as they already do relative to Min/Max Delay today."""
    raw = target_wpm_var.get().strip()
    if not raw:
        return
    try:
        wpm = float(raw)
    except ValueError:
        messagebox.showerror("Invalid Input", "Target WPM must be a number.")
        return
    if wpm <= 0 or wpm > 400:
        messagebox.showerror("Invalid Input", "Target WPM must be between 1 and 400.")
        return

    avg_delay = 12.0 / wpm  # seconds/char: 60s / (wpm * 5 chars-per-word)
    min_delay_var.set(f"{avg_delay * 0.5:.3f}")
    max_delay_var.set(f"{avg_delay * 1.5:.3f}")


# --------------------------------------------
# WINDOW SIZING (Idea #21)
# --------------------------------------------
# The window used to open at a fixed 520x760, which was narrower than
# the widest content (the footer buttons and some settings text), so
# things like the "Stop Typing" button and the Typing Speed hint text
# were cut off until the window was dragged wider. These values are
# only starting guesses; calculate_window_sizes() (called once after the
# whole interface is built) measures the real content and replaces them,
# so the size also adapts to different screens and display scaling.
PREFERRED_WINDOW_WIDTH = 560     # never open narrower than this if the screen allows
PREFERRED_WINDOW_HEIGHT = 820    # tabs scroll, so a taller window just shows more at once
MIN_WINDOW_HEIGHT = 620
SCREEN_MARGIN_WIDTH = 40         # space always left free at the sides of the screen
SCREEN_MARGIN_HEIGHT = 100       # room for the title bar and the Windows taskbar
TAB_SCROLLBAR_ALLOWANCE = 80     # scrollbar + notebook + card padding around tab content

full_view_size = (PREFERRED_WINDOW_WIDTH, PREFERRED_WINDOW_HEIGHT)
full_view_min = (480, MIN_WINDOW_HEIGHT)
compact_view_size = (440, 460)

compact_mode_active = False


def calculate_window_sizes():
    """Measure the finished interface and size the window to fit it.

    Sets the full-view and compact-view sizes, applies the full-view
    size to the window, and centers it on the screen. Sizes are capped
    to the screen so the window can never open bigger than the display.
    Call this once, after every widget has been created."""
    global full_view_size, full_view_min, compact_view_size

    window.update_idletasks()
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    max_w = max(screen_w - SCREEN_MARGIN_WIDTH, 300)
    max_h = max(screen_h - SCREEN_MARGIN_HEIGHT, 300)

    # The tabs scroll vertically only, so the window must be wide enough
    # to show the widest tab, the footer buttons, and the header.
    tabs_w = max(frame.winfo_reqwidth() for frame in tab_frames.values()) + TAB_SCROLLBAR_ALLOWANCE
    footer_w = footer.winfo_reqwidth() + 24    # footer has 12px padding on each side
    header_w = header.winfo_reqwidth()
    notebook_w = notebook.winfo_reqwidth() + 24    # the row of tab titles must fit as well
    needed_w = max(tabs_w, footer_w, header_w, notebook_w)

    full_w = min(max(needed_w, PREFERRED_WINDOW_WIDTH), max_w)
    full_h = min(PREFERRED_WINDOW_HEIGHT, max_h)
    full_view_size = (full_w, full_h)
    full_view_min = (min(needed_w, max_w), min(MIN_WINDOW_HEIGHT, max_h))

    # Compact view: just the header and footer, so measure those.
    compact_w = min(max(footer_w, header_w, 440), max_w)
    compact_h = min(header.winfo_reqheight() + footer.winfo_reqheight() + 20, max_h)
    compact_view_size = (compact_w, compact_h)

    x = max((screen_w - full_w) // 2, 0)
    y = max((screen_h - full_h) // 2 - 30, 0)
    window.minsize(*full_view_min)
    window.geometry(f"{full_w}x{full_h}+{x}+{y}")


def toggle_compact_mode():
    """Toggle between the full tabbed settings view and a small
    status-only view (just the header and the Live Statistics +
    controls footer) -- handy for keeping an eye on progress without
    the full settings panel taking up screen space."""
    global compact_mode_active, full_view_size
    if compact_mode_active:
        window.minsize(*full_view_min)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        window.geometry(f"{full_view_size[0]}x{full_view_size[1]}")
        btn_compact_toggle.configure(text="Compact")
        compact_mode_active = False
    else:
        # Remember the size the user had, so Full View restores it.
        full_view_size = (window.winfo_width(), window.winfo_height())
        notebook.pack_forget()
        window.minsize(*compact_view_size)
        window.geometry(f"{compact_view_size[0]}x{compact_view_size[1]}")
        btn_compact_toggle.configure(text="Full View")
        compact_mode_active = True


def toggle_theme():
    """Toggle the theme preference between 'light' and 'dark', immediately
    persisting it to last_settings.json. Informs the user that the theme
    change will take effect the next time Amanuensis launches."""
    current = theme_var.get()
    new_theme = "dark" if current == "light" else "light"
    theme_var.set(new_theme)
    if "btn_theme_toggle" in globals():
        btn_theme_toggle.configure(text=f"Theme: {new_theme.capitalize()}")
    save_last_settings()
    messagebox.showinfo(
        "Theme Changed",
        f"Theme set to {new_theme.capitalize()}.\n\nThis will take effect the next time you launch Amanuensis.",
    )


# --------------------------------------------
# GUI STATS REFRESH LOOP
# --------------------------------------------
def update_gui():
    if state.is_running:
        if state.total_chars > 0:
            progress = (state.chars_typed / state.total_chars) * 100
            progress_var.set(f"{progress:.1f}%")
            progress_pct_var.set(progress)

        elapsed = time.time() - state.start_time - state.paused_duration
        elapsed_mins = elapsed / 60
        if elapsed_mins > 0 and state.chars_typed > 0:
            words_typed = state.chars_typed / 5
            state.wpm = int(words_typed / elapsed_mins)
            wpm_var.set(f"{state.wpm} WPM")

        chars_left = state.total_chars - state.chars_typed
        if state.wpm > 0:
            words_left = chars_left / 5
            mins_left = words_left / state.wpm

            settings = state.settings
            if settings.get("enable_breaks") and settings.get("break_min") and settings.get("break_max"):
                avg_break_interval = (settings["break_min"] + settings["break_max"]) / 2
                remaining_newlines = max(state.total_newlines - state.newlines_typed, 0)
                estimated_breaks_remaining = remaining_newlines / avg_break_interval if avg_break_interval > 0 else 0
                avg_break_seconds = (settings["break_duration_min"] + settings["break_duration_max"]) / 2
                mins_left += (estimated_breaks_remaining * avg_break_seconds) / 60

            # With a target time and long breaks still ahead, the plan knows the
            # finish time better than the typing pace does (Update #27).
            if state.target_end_time > 0 and (
                    state.session_breaks_left > 0 or state.status.startswith("Session break")):
                mins_left = max(mins_left, (state.target_end_time - time.time()) / 60)

            eta_var.set(f"{int(mins_left)}m {int((mins_left * 60) % 60)}s")

        if state.settings.get("enable_mistakes"):
            next_mistake_var.set(f"~{state.next_mistake_words} words")
        else:
            next_mistake_var.set("Disabled")

        if state.settings.get("enable_breaks"):
            next_break_var.set(f"~{state.next_break_paragraphs} paragraphs")
        else:
            next_break_var.set("Disabled")

    if state.is_running and state.left_mistakes:
        status_var.set(f"{state.status}  |  Mistakes left: {len(state.left_mistakes)}")
    else:
        status_var.set(state.status)
    window.after(500, update_gui)


# --------------------------------------------
# GUI SETUP
# --------------------------------------------
# Design tokens. This is a diagnostic/control-panel tool, not a
# marketing surface, so the palette stays restrained: one calm
# accent (teal) for the primary action, muted amber/brick for the
# secondary actions, and a cool neutral background. Sections use a
# small colored marker instead of card shadows/rounded corners
# (which Tk can't render natively anyway).
# Startup theme check: read directly from last_settings.json before GUI build.
# We do not attempt live re-theming of instantiated widgets; theme applies
# on launch.
startup_theme = "light"
try:
    if os.path.exists(LAST_SETTINGS_PATH):
        with open(LAST_SETTINGS_PATH, "r", encoding="utf-8") as _f:
            _data = json.load(_f)
            _theme_val = str(_data.get("theme", "light")).strip().lower()
            if _theme_val in ("light", "dark"):
                startup_theme = _theme_val
except Exception:
    startup_theme = "light"

LIGHT_PALETTE = {
    "bg": "#eef1f5",
    "panel_bg": "#ffffff",
    "input_bg": "#ffffff",
    "border": "#b8c2cc",
    "border_strong": "#7d8a99",
    "text": "#1b2430",
    "muted": "#647082",
    "accent": "#1f6f5c",
    "accent_dark": "#154d40",
    "amber": "#a8721f",
    "amber_hover": "#85591a",
    "disabled_bg": "#d5dbe2",
    "brick": "#a23b3b",
    "brick_hover": "#7f2a2a",
    "header_bg": "#1b2430",
    "header_hover": "#2c3748",
    "header_border": "#3a4658",
    "header_muted": "#9aa7b8",
    "header_text": "#eef1f5",
}

DARK_PALETTE = {
    "bg": "#181b22",
    "panel_bg": "#222630",
    "input_bg": "#181b22",
    "border": "#64748b",
    "border_strong": "#94a3b8",
    "text": "#e6edf3",
    "muted": "#8d98a7",
    "accent": "#23866b",
    "accent_dark": "#3ec4a0",
    "amber": "#b57c24",
    "amber_hover": "#cf9433",
    "disabled_bg": "#3a4150",
    "brick": "#cf5656",
    "brick_hover": "#e06b6b",
    "header_bg": "#111318",
    "header_hover": "#1d222b",
    "header_border": "#2a3140",
    "header_muted": "#9aa7b8",
    "header_text": "#f0f3f6",
}

PALETTE = DARK_PALETTE if startup_theme == "dark" else LIGHT_PALETTE

FONT_BODY = ("Segoe UI", 9)
FONT_BODY_BOLD = ("Segoe UI", 9, "bold")
FONT_HEADER = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 14, "bold")
FONT_SUBTITLE = ("Segoe UI", 9)
FONT_STAT = ("Segoe UI", 12, "bold")
FONT_STATUS = ("Segoe UI", 10, "bold")

window = tk.Tk()
window.title("Amanuensis")
# Temporary size only; calculate_window_sizes() sets the real one after
# the interface is built (see WINDOW SIZING above).
window.geometry(f"{PREFERRED_WINDOW_WIDTH}x{PREFERRED_WINDOW_HEIGHT}")
window.minsize(*full_view_min)
window.configure(bg=PALETTE["bg"])
window.attributes("-topmost", True)

style = ttk.Style(window)
style.theme_use("clam")

style.configure("TFrame", background=PALETTE["bg"])
style.configure("Panel.TFrame", background=PALETTE["panel_bg"])

style.configure(
    "Card.TLabelframe",
    background=PALETTE["panel_bg"],
    bordercolor=PALETTE["border"],
    lightcolor=PALETTE["border"],
    darkcolor=PALETTE["border"],
    borderwidth=1,
    relief="solid",
)
style.configure(
    "Card.TLabelframe.Label",
    background=PALETTE["bg"],
    foreground=PALETTE["accent_dark"],
    font=FONT_HEADER,
)

style.configure("TLabel", background=PALETTE["panel_bg"], foreground=PALETTE["text"], font=FONT_BODY)
style.configure("Muted.TLabel", background=PALETTE["panel_bg"], foreground=PALETTE["muted"], font=FONT_BODY)
style.configure("StatValue.TLabel", background=PALETTE["panel_bg"], foreground=PALETTE["accent_dark"], font=FONT_STAT)
style.configure("Warning.TLabel", background=PALETTE["panel_bg"], foreground=PALETTE["brick"], font=FONT_BODY)

# Red button used for the Reset This Section buttons on cards that still use ttk.
style.configure(
    "Danger.TButton",
    background=PALETTE["brick"],
    foreground="#ffffff",
    bordercolor=PALETTE["brick"],
    lightcolor=PALETTE["brick"],
    darkcolor=PALETTE["brick"],
    font=FONT_BODY_BOLD,
    padding=(10, 4),
)
style.map(
    "Danger.TButton",
    background=[("active", PALETTE["brick_hover"])],
    foreground=[("active", "#ffffff")],
)

style.configure("TCheckbutton", background=PALETTE["panel_bg"], foreground=PALETTE["text"], font=FONT_BODY)
style.map("TCheckbutton", background=[("active", PALETTE["panel_bg"])])

style.configure(
    "TEntry",
    foreground=PALETTE["text"],
    fieldbackground=PALETTE["input_bg"],
    bordercolor=PALETTE["border"],
    lightcolor=PALETTE["border"],
    darkcolor=PALETTE["border"],
    insertcolor=PALETTE["text"],
    borderwidth=1,
    relief="solid",
    padding=6,
)
style.map(
    "TEntry",
    foreground=[("disabled", PALETTE["muted"])],
    bordercolor=[("focus", PALETTE["accent"])],
    lightcolor=[("focus", PALETTE["accent"])],
    darkcolor=[("focus", PALETTE["accent"])],
)
style.configure(
    "TCombobox",
    foreground=PALETTE["text"],
    fieldbackground=PALETTE["input_bg"],
    background=PALETTE["input_bg"],
    arrowcolor=PALETTE["text"],
    bordercolor=PALETTE["border"],
    lightcolor=PALETTE["border"],
    darkcolor=PALETTE["border"],
    borderwidth=1,
    relief="solid",
    padding=6,
)
style.map(
    "TCombobox",
    foreground=[("readonly", PALETTE["text"])],
    fieldbackground=[("readonly", PALETTE["input_bg"])],
    background=[("readonly", PALETTE["input_bg"])],
    bordercolor=[("focus", PALETTE["accent"])],
    lightcolor=[("focus", PALETTE["accent"])],
    darkcolor=[("focus", PALETTE["accent"])],
    arrowcolor=[("disabled", PALETTE["muted"])],
)

style.configure("TButton", font=FONT_BODY, padding=6)

style.configure("Accent.TButton", background=PALETTE["accent"], foreground="white", font=FONT_STATUS, padding=10)
style.map("Accent.TButton", background=[("active", PALETTE["accent_dark"]), ("disabled", "#a9c4bd")])

style.configure("Amber.TButton", background=PALETTE["amber"], foreground="white", font=FONT_STATUS, padding=10)
style.map("Amber.TButton", background=[("active", "#8a5c17"), ("disabled", "#dcc59c")])

style.configure("Brick.TButton", background=PALETTE["brick"], foreground="white", font=FONT_STATUS, padding=10)
style.map("Brick.TButton", background=[("active", "#7e2e2e"), ("disabled", "#e0b8b8")])

style.configure(
    "TNotebook",
    background=PALETTE["bg"],
    borderwidth=0,
)
style.configure(
    "TNotebook.Tab",
    background=PALETTE["bg"],
    foreground=PALETTE["muted"],
    font=FONT_BODY_BOLD,
    padding=(16, 8),
    borderwidth=0,
)
style.map(
    "TNotebook.Tab",
    background=[("selected", PALETTE["panel_bg"])],
    foreground=[("selected", PALETTE["accent_dark"])],
)

style.configure(
    "Accent.Horizontal.TProgressbar",
    troughcolor=PALETTE["border"],
    background=PALETTE["accent"],
    bordercolor=PALETTE["border"],
    lightcolor=PALETTE["accent"],
    darkcolor=PALETTE["accent"],
)

style.configure(
    "Treeview",
    background=PALETTE["input_bg"],
    foreground=PALETTE["text"],
    fieldbackground=PALETTE["input_bg"],
    borderwidth=0,
    rowheight=26,
)
style.configure(
    "Treeview.Heading",
    background=PALETTE["bg"],
    foreground=PALETTE["text"],
    relief="flat",
    borderwidth=0,
)


# Variables
theme_var = tk.StringVar(value=startup_theme)
input_path_var = tk.StringVar(value="input.txt")
file_queue_var = tk.Variable(value=[])

window_var = tk.StringVar(value="")
lock_window_var = tk.BooleanVar(value=False)
enable_auto_profile_var = tk.BooleanVar(value=False)
auto_profile_rules_var = tk.StringVar(value="[]")  # the rules, saved as JSON text
auto_profile_keyword_var = tk.StringVar(value="")  # new-rule boxes (not saved)
auto_profile_choice_var = tk.StringVar(value="")
auto_profile_status_var = tk.StringVar(value="")
auto_pause_activity_var = tk.BooleanVar(value=False)
activity_sensitivity_var = tk.StringVar(value="20")

min_delay_var = tk.StringVar(value="0.05")
max_delay_var = tk.StringVar(value="0.15")
target_wpm_var = tk.StringVar(value="")
enable_fatigue_var = tk.BooleanVar(value=False)
fatigue_strength_var = tk.StringVar(value="15")
enable_finger_timing_var = tk.BooleanVar(value=True)
enable_typing_moods_var = tk.BooleanVar(value=True)
typing_mood_strength_var = tk.StringVar(value="100")
enable_difficulty_pauses_var = tk.BooleanVar(value=True)
difficulty_pause_strength_var = tk.StringVar(value="100")

enable_target_time_var = tk.BooleanVar(value=False)
target_hours_var = tk.StringVar(value="2")
target_minutes_var = tk.StringVar(value="0")

paragraph_pause_min_var = tk.StringVar(value="10")
paragraph_pause_max_var = tk.StringVar(value="20")
sentence_pause_min_var = tk.StringVar(value="5")
sentence_pause_max_var = tk.StringVar(value="7")
enable_pause_markers_var = tk.BooleanVar(value=True)
enable_format_markers_var = tk.BooleanVar(value=True)

enable_mistakes_var = tk.BooleanVar(value=True)
mistake_min_var = tk.StringVar(value="100")
mistake_max_var = tk.StringVar(value="200")

enable_breaks_var = tk.BooleanVar(value=True)
break_min_var = tk.StringVar(value="3")
break_max_var = tk.StringVar(value="6")
break_duration_min_var = tk.StringVar(value="60")
break_duration_max_var = tk.StringVar(value="180")

enable_typos_var = tk.BooleanVar(value=True)
typo_chance_var = tk.StringVar(value="4")
use_extra_typos_var = tk.BooleanVar(value=False)

enable_paraphrase_var = tk.BooleanVar(value=True)
paraphrase_chance_var = tk.StringVar(value="15")

enable_smart_fixes_var = tk.BooleanVar(value=False)
smart_fix_spacing_var = tk.BooleanVar(value=True)
smart_fix_punctuation_var = tk.BooleanVar(value=True)
smart_fix_capitalization_var = tk.BooleanVar(value=True)
smart_fix_repeated_words_var = tk.BooleanVar(value=True)
smart_fix_visible_chance_var = tk.StringVar(value="50")

enable_leave_mistakes_var = tk.BooleanVar(value=False)
leave_mistake_count_var = tk.StringVar(value="3")
leave_mistakes_typos_var = tk.BooleanVar(value=True)
leave_mistakes_commas_var = tk.BooleanVar(value=True)
leave_mistakes_punctuation_var = tk.BooleanVar(value=True)

hotkey_start_var = tk.StringVar(value="f8")
hotkey_pause_var = tk.StringVar(value="f9")
hotkey_stop_var = tk.StringVar(value="esc")
hotkey_warning_var = tk.StringVar(value="")

log_enabled_var = tk.BooleanVar(value=False)
enable_notify_var = tk.BooleanVar(value=True)
enable_sound_var = tk.BooleanVar(value=True)
enable_update_check_var = tk.BooleanVar(value=True)
skipped_update_version_var = tk.StringVar(value="")
update_status_var = tk.StringVar(value="Not checked yet this session.")

wpm_var = tk.StringVar(value="0 WPM")
eta_var = tk.StringVar(value="0m 0s")
progress_var = tk.StringVar(value="0.0%")
progress_pct_var = tk.DoubleVar(value=0.0)
status_var = tk.StringVar(value="Idle")
next_mistake_var = tk.StringVar(value="~100 words")
next_break_var = tk.StringVar(value="~3 paragraphs")
preview_result_var = tk.StringVar(value="")
resume_status_var = tk.StringVar(value="")

# --------------------------------------------
# RESET-TO-DEFAULT SECTIONS (Idea #19)
# --------------------------------------------
# Which variables belong to which settings card. The default for each
# variable is read right here, straight after the declarations above
# and BEFORE load_last_settings() runs, so the declarations stay the
# single source of truth for default values. When you add a new
# setting, add its variable to the matching card's list below.
SECTION_RESET_VARS = {
    "Auto Profile": [enable_auto_profile_var, auto_profile_rules_var],
    "Activity Auto-Pause": [auto_pause_activity_var, activity_sensitivity_var],
    "Typing Speed": [min_delay_var, max_delay_var, target_wpm_var],
    "Target Time": [enable_target_time_var, target_hours_var, target_minutes_var],
    "Fatigue Drift": [enable_fatigue_var, fatigue_strength_var],
    "Finger & Hand Timing": [enable_finger_timing_var],
    "Flow & Hesitation": [enable_typing_moods_var, typing_mood_strength_var],
    "Natural Pauses": [
        paragraph_pause_min_var,
        paragraph_pause_max_var,
        sentence_pause_min_var,
        sentence_pause_max_var,
        enable_pause_markers_var,
    ],
    "Difficulty Pauses": [enable_difficulty_pauses_var, difficulty_pause_strength_var],
    "Formatting Markers": [enable_format_markers_var],
    "Character Typos": [enable_typos_var, typo_chance_var, use_extra_typos_var],
    "Sentence Reworks & Breaks": [
        enable_mistakes_var,
        mistake_min_var,
        mistake_max_var,
        enable_breaks_var,
        break_min_var,
        break_max_var,
        break_duration_min_var,
        break_duration_max_var,
        enable_paraphrase_var,
        paraphrase_chance_var,
    ],
    "Smart Text Fixes": [
        enable_smart_fixes_var,
        smart_fix_spacing_var,
        smart_fix_punctuation_var,
        smart_fix_capitalization_var,
        smart_fix_repeated_words_var,
        smart_fix_visible_chance_var,
    ],
    "Leave Mistakes For Me": [
        enable_leave_mistakes_var,
        leave_mistake_count_var,
        leave_mistakes_typos_var,
        leave_mistakes_commas_var,
        leave_mistakes_punctuation_var,
    ],
    "Hotkeys": [hotkey_start_var, hotkey_pause_var, hotkey_stop_var],
    "Session Log": [log_enabled_var],
    "On Completion": [enable_notify_var, enable_sound_var],
    "Updates": [enable_update_check_var, skipped_update_version_var],
}

SECTION_DEFAULTS = {
    section_name: [(var, var.get()) for var in section_vars]
    for section_name, section_vars in SECTION_RESET_VARS.items()
}

# --------------------------------------------
# MODERN (CustomTkinter) WIDGET HELPERS
# --------------------------------------------
# Every part of the interface is built with these helpers, so the whole app
# shares one rounded look. Colors come only from PALETTE, so the light/dark
# theme chosen at startup still applies.
BORDER_WIDTH = 4       # outline thickness of entry boxes, text boxes, dropdowns
CHECKBOX_BORDER = 3    # outline thickness of checkboxes
CORNER_RADIUS = 10     # roundness of buttons, entries, and other controls


def modern_font(bold=False, size=13):
    """Font used by the CustomTkinter widgets."""
    return ctk.CTkFont(family="Segoe UI", size=size, weight="bold" if bold else "normal")


class ModernButton(ctk.CTkButton):
    """A CTkButton that also turns grey when it is disabled, so a disabled
    Pause or Stop button never looks clickable."""

    def __init__(self, master, **kwargs):
        self._enabled_fg = kwargs.get("fg_color")
        starts_disabled = kwargs.get("state") == "disabled"
        super().__init__(master, **kwargs)
        if starts_disabled:
            self._show_disabled_look(True)

    def _show_disabled_look(self, disabled):
        if self._enabled_fg in (None, "transparent"):
            return  # outline-only buttons just dim their text
        super().configure(fg_color=PALETTE["disabled_bg"] if disabled else self._enabled_fg)

    def configure(self, require_redraw=False, **kwargs):
        if "state" in kwargs:
            self._show_disabled_look(kwargs["state"] == "disabled")
        super().configure(require_redraw=require_redraw, **kwargs)


BUTTON_LOOKS = {
    # kind: (normal color key, hover color key) in PALETTE
    "primary": ("accent", "accent_dark"),
    "danger": ("brick", "brick_hover"),
    "amber": ("amber", "amber_hover"),
}


def modern_button(parent, text, command, kind="primary", state="normal"):
    """A rounded button. kind is 'primary' (green), 'danger' (red, used for
    every Reset button), 'amber', or 'neutral' (white with an outline)."""
    common = dict(
        text=text,
        command=command,
        state=state,
        height=34,
        corner_radius=CORNER_RADIUS,
        text_color_disabled=PALETTE["muted"],
        font=modern_font(bold=True),
    )
    if kind == "neutral":
        return ModernButton(
            parent,
            fg_color=PALETTE["panel_bg"],
            hover_color=PALETTE["bg"],
            border_width=2,
            border_color=PALETTE["border_strong"],
            text_color=PALETTE["text"],
            **common,
        )
    fg_key, hover_key = BUTTON_LOOKS[kind]
    return ModernButton(
        parent,
        fg_color=PALETTE[fg_key],
        hover_color=PALETTE[hover_key],
        text_color="#ffffff",
        **common,
    )


def modern_label(parent, text="", kind="normal", textvariable=None, wraplength=0):
    """A label. kind is 'normal', 'muted', 'warning', 'stat' (big green
    number), or 'status' (bold)."""
    colors = {
        "normal": PALETTE["text"],
        "muted": PALETTE["muted"],
        "warning": PALETTE["brick"],
        "stat": PALETTE["accent_dark"],
        "status": PALETTE["text"],
    }
    options = {
        "font": modern_font(bold=True, size=16) if kind == "stat" else modern_font(bold=(kind == "status")),
        "text_color": colors[kind],
        "anchor": "w",
        "justify": "left",
        "wraplength": wraplength,
    }
    if textvariable is not None:
        options["textvariable"] = textvariable
    else:
        options["text"] = text
    return ctk.CTkLabel(parent, **options)


def modern_frame(parent, bordered=False):
    """A plain see-through container, or (bordered=True) a rounded outlined
    box used to frame lists and tables."""
    if bordered:
        return ctk.CTkFrame(
            parent,
            corner_radius=CORNER_RADIUS,
            border_width=BORDER_WIDTH,
            fg_color=PALETTE["input_bg"],
            border_color=PALETTE["border_strong"],
        )
    return ctk.CTkFrame(parent, fg_color="transparent")


def make_modern_card(parent, title):
    """A rounded card with a title. Returns the body frame, which is where
    the card's widgets are placed with grid()."""
    outer = ctk.CTkFrame(
        parent,
        corner_radius=16,
        border_width=1,
        fg_color=PALETTE["panel_bg"],
        border_color=PALETTE["border"],
        bg_color=PALETTE["bg"],
    )
    outer.pack(fill="x", pady=(0, 10))
    outer.columnconfigure(0, weight=1)
    ctk.CTkLabel(
        outer,
        text="\u25a0 " + title,
        font=modern_font(bold=True),
        text_color=PALETTE["accent_dark"],
        anchor="w",
    ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 4))
    body = ctk.CTkFrame(outer, fg_color="transparent")
    body.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
    return body


def modern_entry(parent, var, width=10):
    """A short entry box. width is in characters (about 11 pixels each).
    The border turns accent-colored while the box has focus."""
    entry = ctk.CTkEntry(
        parent,
        textvariable=var,
        width=width * 11,
        height=40,
        corner_radius=CORNER_RADIUS,
        border_width=BORDER_WIDTH,
        fg_color=PALETTE["input_bg"],
        border_color=PALETTE["border_strong"],
        text_color=PALETTE["text"],
        font=modern_font(),
    )
    entry.bind("<FocusIn>", lambda _event: entry.configure(border_color=PALETTE["accent"]))
    entry.bind("<FocusOut>", lambda _event: entry.configure(border_color=PALETTE["border_strong"]))
    return entry


def modern_checkbox(parent, text, variable):
    """A checkbox tied to a tk.BooleanVar."""
    return ctk.CTkCheckBox(
        parent,
        text=text,
        variable=variable,
        onvalue=True,
        offvalue=False,
        corner_radius=6,
        border_width=CHECKBOX_BORDER,
        fg_color=PALETTE["accent"],
        hover_color=PALETTE["accent_dark"],
        border_color=PALETTE["border_strong"],
        text_color=PALETTE["text"],
        checkmark_color="#ffffff",
        font=modern_font(),
    )


def modern_separator(parent):
    """A thin rounded divider line. Place it with grid(sticky="we")."""
    return ctk.CTkFrame(parent, height=3, corner_radius=2, fg_color=PALETTE["border"])


def modern_scrollbar(parent, command, horizontal=False):
    """A rounded scrollbar. Its .set method plugs into yscrollcommand /
    xscrollcommand just like a normal scrollbar."""
    return ctk.CTkScrollbar(
        parent,
        orientation="horizontal" if horizontal else "vertical",
        command=command,
        width=40 if horizontal else 14,   # the long side is stretched by grid/pack
        height=14 if horizontal else 40,
        fg_color="transparent",
        button_color=PALETTE["border_strong"],
        button_hover_color=PALETTE["accent"],
    )


def modern_textbox(parent, height=120):
    """A multi-line text box with a rounded outline and its own scrollbar."""
    box = ctk.CTkTextbox(
        parent,
        height=height,
        wrap="word",
        font=modern_font(),
        fg_color=PALETTE["input_bg"],
        text_color=PALETTE["text"],
        corner_radius=CORNER_RADIUS,
        border_width=BORDER_WIDTH,
        border_color=PALETTE["border_strong"],
        scrollbar_button_color=PALETTE["border_strong"],
        scrollbar_button_hover_color=PALETTE["accent"],
    )
    box.bind("<FocusIn>", lambda _event: box.configure(border_color=PALETTE["accent"]))
    box.bind("<FocusOut>", lambda _event: box.configure(border_color=PALETTE["border_strong"]))
    return box


def modern_combobox(parent, var, width=30):
    """A read-only dropdown. Fill it with combo.configure(values=[...])."""
    return ctk.CTkComboBox(
        parent,
        variable=var,
        values=[""],
        state="readonly",
        width=width * 9,
        height=40,
        corner_radius=CORNER_RADIUS,
        border_width=BORDER_WIDTH,
        fg_color=PALETTE["input_bg"],
        border_color=PALETTE["border_strong"],
        button_color=PALETTE["accent"],
        button_hover_color=PALETTE["accent_dark"],
        text_color=PALETTE["text"],
        dropdown_fg_color=PALETTE["panel_bg"],
        dropdown_text_color=PALETTE["text"],
        dropdown_hover_color=PALETTE["bg"],
        font=modern_font(),
        dropdown_font=modern_font(),
    )


def modern_progressbar(parent, percent_var):
    """A rounded progress bar that follows a 0-100 tk.DoubleVar."""
    bar = ctk.CTkProgressBar(
        parent,
        height=14,
        corner_radius=7,
        fg_color=PALETTE["border"],
        progress_color=PALETTE["accent"],
    )

    def _sync(*_args):
        try:
            fraction = float(percent_var.get()) / 100.0
        except (tk.TclError, ValueError):
            fraction = 0.0
        bar.set(min(max(fraction, 0.0), 1.0))

    percent_var.trace_add("write", _sync)
    _sync()
    return bar


def modern_toplevel(parent, title, size, min_size):
    """A rounded-look pop-up window (Preview Run, Session Log Viewer)."""
    top = ctk.CTkToplevel(parent, fg_color=PALETTE["bg"])
    top.title(title)
    top.geometry(size)
    top.minsize(*min_size)
    top.transient(parent)
    return top


def make_tab_area(parent, titles):
    """A pill-shaped tab bar plus one page per title. Returns
    (container, pages, selector): pack the container like any frame, put each
    tab's content in pages[title], and selector is the tab bar itself."""
    container = ctk.CTkFrame(parent, fg_color="transparent")
    selector_holder = ctk.CTkFrame(container, fg_color="transparent")
    body = ctk.CTkFrame(container, fg_color="transparent")
    pages = {title: ctk.CTkFrame(body, fg_color="transparent") for title in titles}

    def show_tab(title):
        for page in pages.values():
            page.pack_forget()
        pages[title].pack(fill="both", expand=True)

    selector = ctk.CTkSegmentedButton(
        selector_holder,
        values=list(titles),
        command=show_tab,
        height=38,
        corner_radius=CORNER_RADIUS + 2,
        fg_color=PALETTE["header_bg"],
        unselected_color=PALETTE["header_bg"],
        unselected_hover_color=PALETTE["header_hover"],
        selected_color=PALETTE["accent"],
        selected_hover_color=PALETTE["accent_dark"],
        text_color=PALETTE["header_text"],
        font=modern_font(bold=True),
    )
    selector.pack(fill="x", padx=4)
    selector_holder.pack(fill="x", pady=(0, 8))
    body.pack(fill="both", expand=True)
    selector.set(titles[0])
    show_tab(titles[0])
    return container, pages, selector


# --- Rounded pop-up dialogs (replace the plain Windows message boxes) ---
def show_modern_dialog(title, message, buttons, kind="info", parent=None, cancel_value=False):
    """Show a modal rounded dialog and return the value of the clicked button.
    buttons is a list of (label, value, button_kind); the first is the default
    (Enter key). Closing the dialog or pressing Escape returns cancel_value."""
    owner = parent if parent is not None else window
    dialog = ctk.CTkToplevel(owner, fg_color=PALETTE["bg"])
    dialog.title(title)
    dialog.resizable(False, False)
    dialog.transient(owner)
    dialog.attributes("-topmost", True)
    result = {"value": cancel_value}

    def choose(value):
        result["value"] = value
        dialog.destroy()

    card = ctk.CTkFrame(
        dialog,
        corner_radius=16,
        border_width=1,
        fg_color=PALETTE["panel_bg"],
        border_color=PALETTE["border"],
        bg_color=PALETTE["bg"],
    )
    card.pack(fill="both", expand=True, padx=14, pady=14)

    icon_colors = {"info": PALETTE["accent"], "error": PALETTE["brick"], "question": PALETTE["amber"]}
    icon_symbols = {"info": "i", "error": "!", "question": "?"}
    icon = ctk.CTkFrame(card, width=44, height=44, corner_radius=22, fg_color=icon_colors[kind])
    icon.pack_propagate(False)
    icon.grid(row=0, column=0, rowspan=2, padx=(18, 12), pady=(18, 8), sticky="n")
    ctk.CTkLabel(
        icon,
        text=icon_symbols[kind],
        fg_color="transparent",
        text_color="#ffffff",
        font=modern_font(bold=True, size=20),
    ).place(relx=0.5, rely=0.5, anchor="center")
    modern_label(card, title, kind="stat").grid(row=0, column=1, padx=(0, 18), pady=(18, 2), sticky="w")
    modern_label(card, message, wraplength=380).grid(row=1, column=1, padx=(0, 18), pady=(0, 8), sticky="w")

    button_row = modern_frame(card)
    button_row.grid(row=2, column=0, columnspan=2, sticky="e", padx=18, pady=(8, 16))
    for label, value, button_kind in buttons:
        modern_button(button_row, label, lambda v=value: choose(v), kind=button_kind).pack(side="left", padx=(8, 0))

    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(cancel_value))
    dialog.bind("<Escape>", lambda _event: choose(cancel_value))
    dialog.bind("<Return>", lambda _event: choose(buttons[0][1]))

    # Centre the dialog over the window that opened it.
    dialog.update_idletasks()
    width, height = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    x = owner.winfo_rootx() + (owner.winfo_width() - width) // 2
    y = owner.winfo_rooty() + (owner.winfo_height() - height) // 3
    dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    dialog.wait_visibility()
    dialog.grab_set()
    dialog.focus_force()
    dialog.wait_window()
    return result["value"]


class ModernMessageBox:
    """Same four calls as tkinter.messagebox (showinfo, showerror, askyesno,
    askokcancel), so every existing call in the app shows a rounded dialog."""

    def showinfo(self, title, message, parent=None, **_options):
        show_modern_dialog(title, message, [("OK", True, "primary")], kind="info", parent=parent)
        return "ok"

    def showerror(self, title, message, parent=None, **_options):
        show_modern_dialog(title, message, [("OK", True, "danger")], kind="error", parent=parent)
        return "ok"

    def askyesno(self, title, message, parent=None, **_options):
        return show_modern_dialog(
            title, message, [("Yes", True, "primary"), ("No", False, "neutral")], kind="question", parent=parent
        )

    def askokcancel(self, title, message, parent=None, **_options):
        return show_modern_dialog(
            title, message, [("OK", True, "primary"), ("Cancel", False, "neutral")], kind="question", parent=parent
        )


messagebox = ModernMessageBox()


# --------------------------------------------
# HEADER BANNER
# --------------------------------------------
header = ctk.CTkFrame(window, fg_color=PALETTE["header_bg"], corner_radius=0)
header.pack(fill="x", side="top")

header_text_frame = ctk.CTkFrame(header, fg_color="transparent")
header_text_frame.pack(side="left", fill="both", expand=True)
ctk.CTkLabel(
    header_text_frame,
    text="Amanuensis",
    text_color=PALETTE["header_text"],
    font=modern_font(bold=True, size=22),
    anchor="w",
).pack(anchor="w", padx=16, pady=(12, 0))
ctk.CTkLabel(
    header_text_frame,
    text="Your human-like typing assistant",
    text_color=PALETTE["header_muted"],
    font=modern_font(),
    anchor="w",
).pack(anchor="w", padx=16, pady=(0, 12))


def make_header_button(text, command):
    """A small outlined button for the dark header banner."""
    return ctk.CTkButton(
        header,
        text=text,
        command=command,
        width=112,
        height=32,
        corner_radius=CORNER_RADIUS,
        fg_color="transparent",
        hover_color=PALETTE["header_hover"],
        border_width=2,
        border_color=PALETTE["header_border"],
        text_color=PALETTE["header_muted"],
        font=modern_font(),
    )


btn_compact_toggle = make_header_button("Compact", toggle_compact_mode)
btn_compact_toggle.pack(side="right", padx=(4, 16), pady=12)

btn_theme_toggle = make_header_button(f"Theme: {theme_var.get().capitalize()}", toggle_theme)
btn_theme_toggle.pack(side="right", padx=(0, 4), pady=12)

# --------------------------------------------
# PERSISTENT FOOTER (Live Statistics + Controls)
# Packed *before* the notebook, with side="bottom", so it always
# keeps its space and is never the part that gets clipped -- this
# fixes the cutoff issue from the previous layout directly.
# --------------------------------------------
footer = ctk.CTkFrame(window, fg_color="transparent")
footer.pack(fill="x", side="bottom", padx=12, pady=(6, 12))

frame_stats = make_modern_card(footer, "Live Statistics")

progress_bar = modern_progressbar(frame_stats, progress_pct_var)
progress_bar.grid(row=0, column=0, columnspan=4, sticky="we", pady=(0, 10))
frame_stats.columnconfigure(0, weight=1)
frame_stats.columnconfigure(1, weight=1)
frame_stats.columnconfigure(2, weight=1)
frame_stats.columnconfigure(3, weight=1)

def _stat_cell(parent, row, col, label_text, value_var):
    modern_label(parent, label_text, kind="muted").grid(row=row, column=col, sticky="w", padx=(0, 4))
    modern_label(parent, textvariable=value_var, kind="stat").grid(
        row=row + 1, column=col, sticky="w", padx=(0, 4), pady=(0, 6)
    )

_stat_cell(frame_stats, 1, 0, "Speed", wpm_var)
_stat_cell(frame_stats, 1, 1, "ETA", eta_var)
_stat_cell(frame_stats, 1, 2, "Progress", progress_var)
_stat_cell(frame_stats, 3, 0, "Next Mistake", next_mistake_var)
_stat_cell(frame_stats, 3, 1, "Next Break", next_break_var)

status_row = modern_frame(frame_stats)
status_row.grid(row=3, column=2, columnspan=2, sticky="e", pady=(0, 6))
ctk.CTkLabel(status_row, text="\u25cf", text_color=PALETTE["accent"], font=modern_font(size=16)).pack(side="left")
modern_label(status_row, textvariable=status_var, kind="status").pack(side="left", padx=(4, 0))

controls_row = modern_frame(footer)
controls_row.pack(fill="x")

btn_start = modern_button(controls_row, "Start Typing", start_typing, kind="primary")
btn_start.pack(side="left", expand=True, padx=(0, 4), fill="x")

btn_preview = modern_button(controls_row, "Preview Run", open_preview_window, kind="neutral")
btn_preview.pack(side="left", expand=True, padx=4, fill="x")

btn_pause = modern_button(controls_row, "Pause Typing", toggle_pause, kind="amber", state=tk.DISABLED)
btn_pause.pack(side="left", expand=True, padx=4, fill="x")

btn_stop = modern_button(controls_row, "Stop Typing", stop_typing, kind="danger", state=tk.DISABLED)
btn_stop.pack(side="left", expand=True, padx=(4, 0), fill="x")

# --------------------------------------------
# VERSION HISTORY DATA (Idea #23)
# --------------------------------------------
# Every release, newest first. The History tab is built from this list.
# It lives in the code (not in a separate file) so it still works when the
# app is packaged into a single .exe. The comment block at the very top of
# this file only keeps the 3 newest entries; this list keeps them all.
# Each entry: version, date (None if it was never written down), a short
# title, and a list of plain-English change lines.
VERSION_HISTORY = [
    {
        "version": "5.7.0",
        "date": "2026-10-13",
        "title": "Auto-pick a profile by window",
        "changes": [
            "Added: an Auto Profile by Window card on the Window tab. Add rules that pair some words from a window title (like \"Google Docs\") with a saved profile. When a run starts, the first rule whose words appear in the target window's title loads that profile, so each program gets its own typing style.",
            "Added: two new settings (on or off, and the list of rules), off and empty by default, restored on launch.",
            "Modified: the target window is the locked window if Lock typing is on, otherwise the window that was active when you pressed the Start hotkey. Clicking the Start button makes Amanuensis itself active, so use the hotkey or lock a window.",
            "Notes: rules are never saved into profiles and are ignored when a profile is loaded, because window titles can be personal. A rule can only use a file from the profiles folder, and an auto-picked profile never changes your hotkeys, theme, or update settings. The session log records only the profile name, never the window title. Preview and the time estimate do not switch profiles.",
        ],
    },
    {
        "version": "5.6.0",
        "date": "2026-10-13",
        "title": "Flow and hesitation states",
        "changes": [
            "Added: typing moods. Amanuensis now drifts between normal, flow (a stretch typed about 15 percent faster), and hesitant (a stretch typed about 25 percent slower). Each stretch lasts a few dozen to a few hundred characters, and the speed eases in and out so it never jumps.",
            "Added: a Flow & Hesitation card on the Speed tab with two new settings (on or off, and Mood Strength from 0 to 200 percent), on by default, saved with your profiles and restored on launch.",
            "Modified: the pre-start time estimate includes the small average change. A session break or research break puts the typist back in the normal state.",
            "Notes: moods change speed only, with no extra pauses, typos, or reworks, and they stack with fatigue drift. Resuming starts in the normal state. The session log records only the mood and the position.",
        ],
    },
    {
        "version": "5.5.0",
        "date": "2026-10-13",
        "title": "Difficulty-aware thinking pauses",
        "changes": [
            "Added: thinking pauses that depend on how hard the text is. Amanuensis stops for a moment before a hard word (long, many syllables, numbers, an acronym, or rare letters) and for a little longer before a very long or packed sentence.",
            "Added: a Difficulty Pauses card on the Speed tab with two new settings (on or off, and Pause Strength from 0 to 300 percent), on by default, saved with your profiles and restored on launch.",
            "Modified: the pre-start time estimate includes the average time of these pauses. Not every hard word pauses, there are always a few words between word pauses, and common long words like \"important\" never count as hard.",
            "Notes: the pause spots are planned once before typing starts, only for text that is still to be typed, so resuming works. Protected text never gets a pause. The session log records only the spot and length, never the words.",
        ],
    },
    {
        "version": "5.4.0",
        "date": "2026-10-13",
        "title": "Formatting markers",
        "changes": [
            "Added: bold and italic markers. Wrap text in [[b]]...[[/b]] or [[i]]...[[/i]] and Amanuensis presses Ctrl+B or Ctrl+I at those spots, so the text comes out bold or italic in Google Docs or Word. The markers are never typed.",
            "Added: a Formatting Markers card on the Speed tab with one new setting, on by default, saved with your profiles and restored on launch. When it is off, the markers are still removed but no shortcuts are pressed (handy for plain web forms).",
            "Added: a warning before typing starts if a marker has no partner (a stray closing marker, a repeated opening marker, or one that is never closed).",
            "Modified: the pre-start time estimate includes the time for each shortcut. Reworks never delete the first formatted word, and no typo, paraphrase, or left mistake can lose or move a formatting change.",
            "Notes: [[b]], [[/b]], [[i]], and [[/i]] are now reserved, so protected text cannot be exactly b or i. Preview does not press shortcuts. An unclosed marker is closed after the last character. When resuming, click right after the last typed character so the formatting carries on.",
        ],
    },
    {
        "version": "5.3.0",
        "date": "2026-10-12",
        "title": "Finger and hand timing",
        "changes": [
            "Added: a Finger & Hand Timing card on the Speed tab. Two letters typed with opposite hands in a row come a little sooner, and two different letters typed with the same finger (like \"ed\" or \"lo\") come a little later, like on a real keyboard.",
            "Added: one new setting (enable finger and hand timing), on by default, saved with your profiles and restored on launch.",
            "Modified: the pre-start time estimate includes the small net change. The 16 common letter pairs keep their quick fixed speed.",
            "Notes: only letter-to-letter pairs change. Spaces, numbers, punctuation, and doubled letters keep their normal timing, and fatigue drift is applied after this change.",
        ],
    },
    {
        "version": "5.2.0",
        "date": "2026-10-12",
        "title": "Resume across sessions",
        "changes": [
            "Added: resume_state.json v2 format storing typing progress per file, keyed by normalized path.",
            "Added: SHA-256 prefix hash validation to detect if text before the stopping point changed.",
            "Added: autosave checkpoint at sentence-ending punctuation (. ! ? and newlines) while typing, so recovery works even if the app closes unexpectedly.",
            "Added: saved progress status line and Clear Saved Progress button with confirmation in the Input File card.",
            "Added: atomic writes using temporary files and a background-thread lock to prevent file corruption.",
        ],
    },
    {
        "version": "5.1.0",
        "date": "2026-10-11",
        "title": "Target time till completion",
        "changes": [
            "Added: a Target Time card on the Speed tab. Pick how long the whole run should take, in hours and minutes. The spare time is spent on long session breaks, like someone writing over several sittings instead of all at once.",
            "Added: a longer target means more and longer breaks. A target of several hours can include breaks of an hour or more. Breaks happen at the start of a paragraph or sentence, never inside protected text.",
            "Added: break lengths adjust as the run goes, so the run finishes close to your target. Time you spend paused yourself is not counted.",
            "Added: the pre-start time estimate describes the plan, and a warning appears before typing starts if the target is shorter than the typing time.",
            "Added: three new settings (enable, hours, minutes), saved with your profiles and restored on launch.",
            "Modified: with a batch queue, the target is for the whole run and is shared between the files by size. The ETA follows the plan while long breaks are still ahead.",
            "Notes: Preview ignores the target. Amanuensis never types faster than your speed settings, so a target that is too short cannot be reached. Keep the computer awake during a long run, and turn on Target Window Lock so typing never lands in the wrong window after a break.",
        ],
    },
    {
        "version": "5.0.2",
        "date": "2026-10-11",
        "title": "Release notes from version history",
        "changes": [
            "Added: the description on each GitHub release page is now written automatically from the newest version history entry, so the code, this History tab, and the release page always match.",
            "Modified: a release now stops before publishing if the newest history entry is missing or its version does not match the tag.",
            "Notes: no change to how the app works, no new settings, and no new packages.",
        ],
    },
    {
        "version": "5.0.1",
        "date": "2026-10-10",
        "title": "Guard invalid saved settings",
        "changes": [
            "Fixed: malformed or non-dictionary saved settings/profile JSON is ignored instead of crashing the app.",
            "Fixed: invalid saved profile values are skipped before applying them, so the app falls back to safe defaults.",
        ],
    },
    {
        "version": "5.0.0",

        "date": "2026-10-10",
        "title": "Public release preparation",
        "changes": [
            "Added: exported session logs now stop spreadsheet programs from running log text as a formula, and the log records only the input file's name instead of its full folder path.",
            "Added: saved profiles no longer include folder paths, and loading a profile ignores them, so profiles are safe to share.",
            "Modified: a damaged or hand-edited settings or profile file is now rejected with a message (or quietly ignored at startup) instead of crashing the app.",
            "Modified: the update prompt double-checks the GitHub address before opening it in your browser.",
            "Fixed: removed an unused variable that made the automatic code check report a warning.",
            "Notes: the project is ready for public release, with a security policy, contributor rules, automatic dependency and code scanning, and release checksums you can verify. See SECURITY.md.",
        ],
    },
    {
        "version": "4.4.0",
        "date": "2026-10-10",
        "title": "Update prompts",
        "changes": [
            "Added: an Updates card on the Data tab. The app can check GitHub for a newer version when it starts, and a Check for Updates Now button checks any time.",
            "Added: when a newer version exists, a prompt lets you download it, visit the GitHub page, skip that version, or wait until later.",
            "Added: downloads are saved as a separate file next to the app, checked against GitHub's checksum, and never replace or restart the app by themselves.",
            "Added: two new settings (check on startup, skipped version).",
            "Notes: only a version number is requested. Nothing from your text or settings is sent. The prompt never appears while a run is active.",
        ],
    },
    {
        "version": "4.3.0",
        "date": "2026-10-09",
        "title": "Leave Mistakes For Me",
        "changes": [
            "Added: Leave Mistakes For Me card on the Mistakes tab. It purposely leaves a few mistakes in the typed text for you to find and fix yourself. Off by default.",
            "Added: three groups you can turn on or off: typos (wrong, swapped, doubled, or missing letters, and joined words), missing commas, and missing punctuation (periods, ? ! and apostrophes).",
            "Added: a Mistakes Left For You window that opens when the run ends. It lists each mistake with its line number, what is on screen, and what it should be. You can copy it or save it as a text file.",
            "Added: a live Mistakes left count in the status line, and a View Last Mistake List button.",
            "Notes: mistakes are spread out across the file and never touch [[protected text]], web addresses, emails, paths, or numbers. The typer never fixes a left mistake by accident.",
        ],
    },
    {
        "version": "4.2.0",
        "date": "2026-10-09",
        "title": "Version History tab",
        "changes": [
            "Added: a History tab that shows the 10 newest releases, newest first.",
            "Added: a Current Version card at the top of the History tab.",
        ],
    },
    {
        "version": "4.1.0",
        "date": "2026-10-09",
        "title": "Smart Text Fixes",
        "changes": [
            "Added: Smart Text Fixes card on the Mistakes tab. Local, rule-based proofreading with no AI and no internet.",
            "Added: fixes for extra, trailing, and missing spaces, spaces before punctuation, missing commas after openers like \"However\", missing end-of-paragraph periods, doubled punctuation, sentence capitalization, a lowercase \"i\", and repeated words.",
            "Added: four on/off groups (off by default), a \"Show fixes being noticed\" chance, and a Review Fixes button that lists every planned change.",
            "Notes: text inside [[protected markers]] is never changed.",
        ],
    },
    {
        "version": "4.0.0",
        "date": "2026-10-08",
        "title": "Whole app converted to CustomTkinter",
        "changes": [
            "Modified: every part of the interface now uses the rounded CustomTkinter look, including the pop-up windows and message dialogs.",
            "Modified: entry, text, and dropdown borders are thicker, and Reset All Settings is red.",
            "Removed: the old ttk fallback. CustomTkinter is now a required package.",
        ],
    },
    {
        "version": "3.23.0",
        "date": "2026-10-07",
        "title": "Darker borders and red Reset buttons",
        "changes": [
            "Modified: entry boxes and checkboxes on the Hotkeys tab have a thicker, darker outline.",
            "Modified: every Reset This Section button is red.",
            "Notes: CustomTkinter adopted as the standard look.",
        ],
    },
    {
        "version": "3.22.0",
        "date": None,
        "title": "CustomTkinter look test",
        "changes": [
            "Added: a rounded CustomTkinter look test on the Hotkeys tab.",
        ],
    },
    {
        "version": "3.21.0",
        "date": None,
        "title": "Six topic tabs",
        "changes": [
            "Modified: settings are split across six tabs (Input, Window, Speed, Mistakes, Hotkeys, Data) with 1 to 3 related cards each.",
            "Modified: the window size is measured from all of the tabs.",
        ],
    },
    {
        "version": "3.20.0",
        "date": None,
        "title": "Window opens at the right size",
        "changes": [
            "Fixed: the window opened too small and cut off text. It now measures the real content at startup, opens centered, and never opens bigger than the screen.",
            "Modified: compact mode is sized from the measured header and footer.",
        ],
    },
    {
        "version": "3.19.0",
        "date": None,
        "title": "Reset buttons",
        "changes": [
            "Added: a Reset This Section button on every settings card.",
            "Added: Reset All Settings in Profiles.",
        ],
    },
    {
        "version": "3.17.0",
        "date": None,
        "title": "Inline pause markers",
        "changes": [
            "Added: {{pause:N}} markers that force a pause at an exact spot in the text. Decimals work, with a 600 second cap.",
            "Added: an \"Enable inline pause markers\" toggle in the Natural Pauses card.",
            "Added: a warning before the run for markers that are invalid, which are typed as plain text.",
        ],
    },
    {
        "version": "3.16.0",
        "date": None,
        "title": "More typo types",
        "changes": [
            "Added: a \"Use extra typo types\" toggle for swapped letters, doubled letters, and missed spaces.",
            "Added: a delayed notice and backspace correction, like a real person catching it late.",
        ],
    },
    {
        "version": "3.15.0",
        "date": None,
        "title": "Fatigue drift",
        "changes": [
            "Added: optional fatigue drift. Typing slowly gets slower over a long session, with a configurable slowdown percentage.",
            "Added: partial recovery after a research break, and time estimates that include the slowdown.",
        ],
    },
    {
        "version": "3.14.0",
        "date": None,
        "title": "Activity auto-pause",
        "changes": [
            "Added: typing pauses when the real mouse moves during a run, with a configurable pixel sensitivity and a normal resume control.",
        ],
    },
    {
        "version": "3.13.0",
        "date": None,
        "title": "Automatic releases",
        "changes": [
            "Added: a GitHub Actions workflow that builds the Windows .exe when a version tag is pushed and attaches it to the GitHub Release.",
        ],
    },
    {
        "version": "3.12.0",
        "date": None,
        "title": "Research break countdown",
        "changes": [
            "Added: a live countdown in the status line during research breaks.",
        ],
    },
    {
        "version": "3.11.0",
        "date": None,
        "title": "All characters supported",
        "changes": [
            "Fixed: em dashes, en dashes, curly quotes, ellipses, and other non-ASCII characters are now typed correctly.",
        ],
    },
    {
        "version": "3.10.0",
        "date": None,
        "title": "Paragraph paraphrase and correct",
        "changes": [
            "Added: at the end of a paragraph, the app can go back, reword a sentence with synonyms, then correct it to the original text by deleting words and retyping.",
        ],
    },
    {
        "version": "3.9.0",
        "date": None,
        "title": "Preview Run",
        "changes": [
            "Added: a Preview Run button that shows the mistakes and pauses in a small window without sending any real keystrokes.",
        ],
    },
    {
        "version": "3.8.0",
        "date": None,
        "title": "Dark mode",
        "changes": [
            "Added: Light and Dark color palettes with a header toggle button. The choice applies on the next launch.",
        ],
    },
    {
        "version": "3.7.0",
        "date": None,
        "title": "Session log viewer",
        "changes": [
            "Added: View Last Session Log and Open Log File buttons that show past CSV logs in a table inside the app.",
        ],
    },
    {
        "version": "3.6.0",
        "date": None,
        "title": "Paste text directly",
        "changes": [
            "Added: an \"Or paste text directly\" box and a Use Pasted Text button, so a .txt file is not required.",
        ],
    },
    {
        "version": "3.5.0",
        "date": None,
        "title": "Batch mode",
        "changes": [
            "Added: an Input File card with a queue of files that run one after another.",
            "Added: a countdown and resume support for each file, and a single notification at the end.",
        ],
    },
    {
        "version": "3.3.0",
        "date": None,
        "title": "Completion alerts, compact mode, license",
        "changes": [
            "Added: an On Completion card with a notification and a sound, as separate toggles.",
            "Added: a Compact button in the header for a small status-only view.",
            "Added: the MIT license.",
        ],
    },
]

# Always read from the newest entry so the version shown in the app and the
# list above can never disagree.
APP_VERSION = VERSION_HISTORY[0]["version"]

# --------------------------------------------
# TABBED SETTINGS (fills remaining space above the footer)
# --------------------------------------------
TAB_TITLES = ["Input", "Window", "Speed", "Mistakes", "Hotkeys", "Data", "History"]
notebook, tab_pages, tab_selector = make_tab_area(window, TAB_TITLES)
notebook.pack(fill="both", expand=True, padx=12, pady=(0, 6))


def make_scrollable_tab(parent):
    """Wrap a notebook tab in a scrollable canvas.

    Root cause note: a tab can stack several cards, and their
    combined natural height doesn't reliably fit in a fixed-height
    window across different screens/DPI settings. Tk doesn't shrink
    or warn when that happens -- it silently stops drawing whatever
    doesn't fit. This makes the tab scrollable so nothing can be
    silently clipped again.
    """
    outer = tk.Frame(parent, bg=PALETTE["bg"])
    outer.pack(fill="both", expand=True)

    canvas = tk.Canvas(outer, bg=PALETTE["bg"], highlightthickness=0)
    scrollbar = modern_scrollbar(outer, canvas.yview)
    inner = tk.Frame(canvas, bg=PALETTE["bg"], padx=4, pady=10)

    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def _on_enter(_event):
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

    def _on_leave(_event):
        canvas.unbind_all("<MouseWheel>")

    canvas.bind("<Enter>", _on_enter)
    canvas.bind("<Leave>", _on_leave)

    return inner, canvas


# One page per topic (Idea #20), built by make_tab_area() above. Every page is
# wrapped in its own scrollable canvas, exactly like the old two tabs were.
tab_frames = {}     # tab title -> inner frame that cards are placed in
tab_canvases = {}   # tab title -> its scroll canvas

for _tab_title in TAB_TITLES:
    tab_frames[_tab_title], tab_canvases[_tab_title] = make_scrollable_tab(tab_pages[_tab_title])

tab_input = tab_frames["Input"]
tab_window = tab_frames["Window"]
tab_speed = tab_frames["Speed"]
tab_mistakes = tab_frames["Mistakes"]
tab_hotkeys = tab_frames["Hotkeys"]
tab_data = tab_frames["Data"]
tab_history = tab_frames["History"]
tab_window_canvas = tab_canvases["Window"]


# --- Tab: Input ---
frame_file = make_modern_card(tab_input, "Input File")

# --- Tab: Window (keeps typing going to the right place) ---
frame_window = make_modern_card(tab_window, "Target Window Lock")
frame_auto_profile = make_modern_card(tab_window, "Auto Profile by Window")
frame_activity = make_modern_card(tab_window, "Activity Auto-Pause")

# --- Tab: Speed (how fast and how steadily it types) ---
frame_settings = make_modern_card(tab_speed, "Typing Speed")
frame_target_time = make_modern_card(tab_speed, "Target Time")
frame_fatigue = make_modern_card(tab_speed, "Fatigue Drift")
frame_finger_timing = make_modern_card(tab_speed, "Finger & Hand Timing")
frame_typing_moods = make_modern_card(tab_speed, "Flow & Hesitation")
frame_pauses = make_modern_card(tab_speed, "Natural Pauses")
frame_difficulty_pauses = make_modern_card(tab_speed, "Difficulty Pauses")
frame_format_markers = make_modern_card(tab_speed, "Formatting Markers")

# --- Tab: Mistakes (typos, reworks, and research breaks) ---
frame_typos = make_modern_card(tab_mistakes, "Character Typos")
frame_mistakes = make_modern_card(tab_mistakes, "Sentence Reworks & Breaks")
frame_smart_fixes = make_modern_card(tab_mistakes, "Smart Text Fixes")
frame_leave_mistakes = make_modern_card(tab_mistakes, "Leave Mistakes For Me")

# --- Tab: Hotkeys (controls and what happens when a run ends) ---
frame_hotkeys = make_modern_card(tab_hotkeys, "Hotkeys")
frame_completion = make_modern_card(tab_hotkeys, "On Completion")

# --- Tab: Data (saved settings and logs) ---
frame_profiles = make_modern_card(tab_data, "Profiles")
frame_logging = make_modern_card(tab_data, "Session Log")
frame_updates = make_modern_card(tab_data, "Updates")

# --- Tab: History (what changed in each release; read-only, no settings) ---
frame_current_version = make_modern_card(tab_history, "Current Version")
frame_release_list = make_modern_card(tab_history, "Past Releases")

# --- Input file ---
modern_label(frame_file, "Single File (used if queue is empty):").grid(
    row=0, column=0, columnspan=2, sticky="w", pady=(0, 2)
)
modern_entry(frame_file, input_path_var, width=40).grid(row=1, column=0, padx=(0, 8), sticky="we")
modern_button(frame_file, "Browse...", browse_input_file, kind="neutral").grid(row=1, column=1)
frame_file.columnconfigure(0, weight=1)

modern_label(frame_file, "Batch Queue (optional — runs each file in order):").grid(
    row=2, column=0, columnspan=2, sticky="w", pady=(8, 2)
)

queue_frame = modern_frame(frame_file, bordered=True)
queue_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(0, 4))
queue_frame.columnconfigure(0, weight=1)

file_queue_listbox = tk.Listbox(
    queue_frame,
    listvariable=file_queue_var,
    height=4,
    selectmode=tk.EXTENDED,
    font=FONT_BODY,
    bg=PALETTE["input_bg"],
    fg=PALETTE["text"],
    selectbackground=PALETTE["accent"],
    selectforeground="#ffffff",
    relief="flat",
    bd=0,
    highlightthickness=0,
    activestyle="none",
)
queue_scroll = modern_scrollbar(queue_frame, file_queue_listbox.yview)
file_queue_listbox.configure(yscrollcommand=queue_scroll.set)
file_queue_listbox.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=12)
queue_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=10)


def _queue_listbox_scroll(event):
    file_queue_listbox.yview_scroll(int(-1 * (event.delta / 120)), "units")
    return "break"


file_queue_listbox.bind("<MouseWheel>", _queue_listbox_scroll)

queue_btn_frame = modern_frame(frame_file)
queue_btn_frame.grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))

modern_button(queue_btn_frame, "Add Files...", add_queue_files, kind="neutral").pack(side="left", padx=(0, 6))
modern_button(queue_btn_frame, "Remove Selected", remove_selected_queue_files, kind="neutral").pack(side="left", padx=(0, 6))
modern_button(queue_btn_frame, "Clear Queue", clear_queue_files, kind="neutral").pack(side="left")

# --- Paste text directly ---
modern_label(frame_file, "Or paste text directly:").grid(
    row=5, column=0, columnspan=2, sticky="w", pady=(8, 2)
)

paste_text_box = modern_textbox(frame_file, height=130)
paste_text_box.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(0, 4))

modern_button(frame_file, "Use Pasted Text", use_pasted_text).grid(
    row=7, column=0, columnspan=2, sticky="w", pady=(0, 8)
)

modern_button(frame_file, "Preview Time Estimate", preview_estimate, kind="neutral").grid(
    row=8, column=0, columnspan=2, sticky="w", pady=(4, 0)
)
modern_label(frame_file, textvariable=preview_result_var, kind="muted", wraplength=420).grid(
    row=9, column=0, columnspan=2, sticky="w", pady=(4, 0)
)
modern_label(
    frame_file,
    "Tip: wrap exact text in [[double brackets]] to type it with no typos or reworks,\n"
    "e.g. [[ACC-4471-2290]] \u2014 useful for account numbers, URLs, or exact values.",
    kind="muted",
    wraplength=420,
).grid(row=10, column=0, columnspan=2, sticky="w", pady=(4, 0))
modern_label(frame_file, textvariable=resume_status_var, kind="muted", wraplength=420).grid(
    row=11, column=0, columnspan=2, sticky="w", pady=(8, 0)
)
modern_button(frame_file, "Clear Saved Progress", clear_saved_progress, kind="neutral").grid(
    row=12, column=0, columnspan=2, sticky="w", pady=(4, 0)
)


# --- Target window lock ---
if HAS_PYGETWINDOW:
    window_combo = modern_combobox(frame_window, window_var, width=30)
    window_combo.grid(row=0, column=0, padx=(0, 8), sticky="we")

    def _window_combo_no_scroll(event):
        """ttk.Combobox has a built-in binding that cycles its
        selected value on mouse-wheel scroll. That's normally handy,
        but here it means scrolling through the Window tab can
        silently change which window is locked. This instance-level
        binding takes priority over that class binding, so instead
        the scroll is forwarded to the page and the dropdown's value
        is left alone."""
        tab_window_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    window_combo.bind("<MouseWheel>", _window_combo_no_scroll)

    modern_button(frame_window, "Refresh", refresh_window_list, kind="neutral").grid(row=0, column=1)
    modern_checkbox(frame_window, "Lock typing to this window", lock_window_var).grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
    )
    frame_window.columnconfigure(0, weight=1)
    refresh_window_list()
else:
    window_combo = None
    modern_label(
        frame_window,
        "pygetwindow not installed \u2014 window lock disabled.\nInstall with: pip install pygetwindow",
        kind="warning",
    ).pack(anchor="w")

# --- Auto profile by window (Update #30) ---
modern_checkbox(
    frame_auto_profile,
    "Pick a typing profile automatically from the window title",
    enable_auto_profile_var,
).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
modern_label(
    frame_auto_profile,
    "When a run starts, the first rule whose words appear in the target window's title loads its "
    "profile. The target is the locked window if lock is on, otherwise the window that was active "
    "when you pressed the Start hotkey. Keep your profiles in the profiles folder.",
    kind="muted",
    wraplength=420,
).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))

modern_label(frame_auto_profile, "Window title contains:").grid(row=2, column=0, columnspan=2, sticky="w")
modern_entry(frame_auto_profile, auto_profile_keyword_var, width=30).grid(
    row=3, column=0, columnspan=2, sticky="w", pady=(4, 8)
)

modern_label(frame_auto_profile, "Use this profile:").grid(row=4, column=0, columnspan=2, sticky="w")
auto_profile_combo = modern_combobox(frame_auto_profile, auto_profile_choice_var, width=30)
auto_profile_combo.grid(row=5, column=0, padx=(0, 8), pady=(4, 8), sticky="we")
modern_button(frame_auto_profile, "Refresh", refresh_auto_profile_choices, kind="neutral").grid(
    row=5, column=1, pady=(4, 8)
)
modern_button(frame_auto_profile, "Add Rule", add_auto_profile_rule).grid(
    row=6, column=0, columnspan=2, sticky="w", pady=(0, 8)
)

modern_label(frame_auto_profile, "Rules (checked from the top, the first match wins):").grid(
    row=7, column=0, columnspan=2, sticky="w", pady=(0, 2)
)
auto_profile_list_frame = modern_frame(frame_auto_profile, bordered=True)
auto_profile_list_frame.grid(row=8, column=0, columnspan=2, sticky="nsew", pady=(0, 4))
auto_profile_list_frame.columnconfigure(0, weight=1)

auto_profile_listbox = tk.Listbox(
    auto_profile_list_frame,
    height=4,
    selectmode=tk.EXTENDED,
    font=FONT_BODY,
    bg=PALETTE["input_bg"],
    fg=PALETTE["text"],
    selectbackground=PALETTE["accent"],
    selectforeground="#ffffff",
    relief="flat",
    bd=0,
    highlightthickness=0,
    activestyle="none",
    exportselection=False,
)
auto_profile_scroll = modern_scrollbar(auto_profile_list_frame, auto_profile_listbox.yview)
auto_profile_listbox.configure(yscrollcommand=auto_profile_scroll.set)
auto_profile_listbox.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=12)
auto_profile_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=10)


def _auto_profile_listbox_scroll(event):
    auto_profile_listbox.yview_scroll(int(-1 * (event.delta / 120)), "units")
    return "break"


auto_profile_listbox.bind("<MouseWheel>", _auto_profile_listbox_scroll)

modern_button(
    frame_auto_profile, "Remove Selected", remove_selected_auto_profile_rules, kind="neutral"
).grid(row=9, column=0, columnspan=2, sticky="w", pady=(0, 4))
modern_label(frame_auto_profile, textvariable=auto_profile_status_var, kind="muted", wraplength=420).grid(
    row=10, column=0, columnspan=2, sticky="w", pady=(4, 0)
)
frame_auto_profile.columnconfigure(0, weight=1)
refresh_auto_profile_choices()
refresh_auto_profile_listbox()
auto_profile_rules_var.trace_add("write", refresh_auto_profile_listbox)

# --- Activity auto-pause ---
modern_checkbox(
    frame_activity, "Auto-pause on mouse/keyboard activity", auto_pause_activity_var
).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

modern_label(frame_activity, "Movement Sensitivity (px):").grid(row=1, column=0, sticky="w", padx=(0, 8))
entry_activity_sensitivity = modern_entry(frame_activity, activity_sensitivity_var, width=8)
entry_activity_sensitivity.grid(row=1, column=1, sticky="w")

modern_label(
    frame_activity,
    "Automatically pauses typing if the mouse is moved beyond this distance,\n"
    "preventing accidental input mixing. Resume with the Resume button or Pause hotkey.",
    kind="muted",
    wraplength=420,
).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

# --- Typing settings ---
frame_settings.columnconfigure(0, weight=1)
frame_settings.columnconfigure(1, weight=1)

modern_label(frame_settings, "Min Delay (s)").grid(row=0, column=0, sticky="w")
entry_min_delay = modern_entry(frame_settings, min_delay_var)
entry_min_delay.grid(row=1, column=0, sticky="w", pady=(4, 0))

modern_label(frame_settings, "Max Delay (s)").grid(row=0, column=1, sticky="w", padx=(16, 0))
entry_max_delay = modern_entry(frame_settings, max_delay_var)
entry_max_delay.grid(row=1, column=1, sticky="w", padx=(16, 0), pady=(4, 0))

modern_separator(frame_settings).grid(row=2, column=0, columnspan=2, sticky="we", pady=10)

modern_label(frame_settings, "Target WPM (optional)").grid(row=3, column=0, sticky="w")
entry_target_wpm = modern_entry(frame_settings, target_wpm_var)
entry_target_wpm.grid(row=4, column=0, sticky="w")
modern_button(frame_settings, "Apply WPM", apply_target_wpm, kind="neutral").grid(
    row=4, column=1, sticky="w", padx=(16, 0)
)
entry_target_wpm.bind("<FocusOut>", apply_target_wpm)
modern_label(
    frame_settings,
    "Sets Min/Max Delay from a words-per-minute target (5 chars/word).\n"
    "Raw typing speed only -- pauses, breaks, and mistakes are separate\n"
    "and will bring your overall session average below this number.",
    kind="muted",
).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

# --- Target time (Update #27) ---
modern_checkbox(
    frame_target_time, "Enable target time till completion", enable_target_time_var
).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

modern_label(frame_target_time, "Hours").grid(row=1, column=0, sticky="w")
modern_label(frame_target_time, "Minutes").grid(row=1, column=1, sticky="w", padx=(16, 0))
entry_target_hours = modern_entry(frame_target_time, target_hours_var, width=8)
entry_target_hours.grid(row=2, column=0, sticky="w", pady=(4, 0))
entry_target_minutes = modern_entry(frame_target_time, target_minutes_var, width=8)
entry_target_minutes.grid(row=2, column=1, sticky="w", padx=(16, 0), pady=(4, 0))

modern_label(
    frame_target_time,
    "Sets how long the whole run should take, from the first key to the last. "
    "The spare time is spent on long breaks, like someone writing over several "
    "sittings. The longer the target, the longer the breaks: a target of several "
    "hours can have breaks of an hour or more.\n\n"
    "Breaks happen at the start of a paragraph or sentence, never inside protected "
    "text. Time you spend paused yourself does not count. Amanuensis never types "
    "faster than your speed settings, so a target that is too short cannot be "
    "reached. Preview ignores this setting.\n\n"
    "Tip: turn on Target Window Lock (Window tab) so typing never lands in the "
    "wrong window after a break, and keep the computer awake during a long run.",
    kind="muted",
    wraplength=420,
).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

# --- Fatigue drift ---
modern_checkbox(
    frame_fatigue, "Enable fatigue drift", enable_fatigue_var
).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

modern_label(frame_fatigue, "Fatigue Strength (%):").grid(row=1, column=0, sticky="w", padx=(0, 8))
entry_fatigue_strength = modern_entry(frame_fatigue, fatigue_strength_var, width=8)
entry_fatigue_strength.grid(row=1, column=1, sticky="w")

modern_label(
    frame_fatigue,
    "Typing speed gradually slows down during long continuous typing stretches\n"
    "and partly recovers after research breaks, simulating natural fatigue (0–40%).",
    kind="muted",
    wraplength=420,
).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

# --- Finger and hand timing ---
modern_checkbox(
    frame_finger_timing, "Enable finger and hand timing", enable_finger_timing_var
).grid(row=0, column=0, sticky="w", pady=(0, 6))

modern_label(
    frame_finger_timing,
    "Types like real hands on a QWERTY keyboard. When two letters in a row use "
    "opposite hands (like the \"ti\" in \"time\"), the second one comes a little "
    "sooner. When two different letters use the same finger (like \"ed\" or \"lo\"), "
    "it comes a little later.\n\n"
    "Only letters are affected. Spaces, numbers, punctuation, and doubled letters "
    "keep their normal timing, and the common letter pairs keep their quick speed.",
    kind="muted",
    wraplength=420,
).grid(row=1, column=0, sticky="w", pady=(6, 0))

# --- Flow and hesitation states (Update #31) ---
modern_checkbox(
    frame_typing_moods, "Enable flow and hesitation states", enable_typing_moods_var
).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

modern_label(frame_typing_moods, "Mood Strength (%):").grid(row=1, column=0, sticky="w", padx=(0, 8))
entry_typing_mood_strength = modern_entry(frame_typing_moods, typing_mood_strength_var, width=8)
entry_typing_mood_strength.grid(row=1, column=1, sticky="w")

modern_label(
    frame_typing_moods,
    "Real typing has moods. Sometimes the words come easily and you speed up "
    "(flow). Other times you second-guess yourself and slow down (hesitation). "
    "Amanuensis drifts between normal, flow, and hesitant stretches that last a "
    "few dozen to a few hundred characters, and the speed eases in and out so it "
    "never jumps.\n\n"
    "100% is the normal size (flow is about 15% quicker, hesitation about 25% "
    "slower). Use less for a steadier typist or more for bigger swings (0 to 200). "
    "A long break puts the typist back in the normal state, and the time estimate "
    "includes the small average change.",
    kind="muted",
    wraplength=420,
).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

# --- Natural pauses (always on, not tied to any enable checkbox) ---
modern_label(frame_pauses, "Paragraph pause, min (s)").grid(row=0, column=0, sticky="w")
modern_label(frame_pauses, "Paragraph pause, max (s)").grid(row=0, column=1, sticky="w", padx=(16, 0))
entry_paragraph_pause_min = modern_entry(frame_pauses, paragraph_pause_min_var)
entry_paragraph_pause_min.grid(row=1, column=0, sticky="w")
entry_paragraph_pause_max = modern_entry(frame_pauses, paragraph_pause_max_var)
entry_paragraph_pause_max.grid(row=1, column=1, sticky="w", padx=(16, 0))
modern_label(
    frame_pauses, "A short pause to think happens at every paragraph, on top of any research break.",
    kind="muted",
).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 10))

modern_label(frame_pauses, "Sentence-end pause, min (s)").grid(row=3, column=0, sticky="w")
modern_label(frame_pauses, "Sentence-end pause, max (s)").grid(row=3, column=1, sticky="w", padx=(16, 0))
entry_sentence_pause_min = modern_entry(frame_pauses, sentence_pause_min_var)
entry_sentence_pause_min.grid(row=4, column=0, sticky="w")
entry_sentence_pause_max = modern_entry(frame_pauses, sentence_pause_max_var)
entry_sentence_pause_max.grid(row=4, column=1, sticky="w", padx=(16, 0))

modern_separator(frame_pauses).grid(row=5, column=0, columnspan=2, sticky="we", pady=10)

modern_checkbox(
    frame_pauses, "Enable inline pause markers", enable_pause_markers_var
).grid(row=6, column=0, columnspan=2, sticky="w")
modern_label(
    frame_pauses,
    "Put {{pause:5}} anywhere in your text to wait 5 seconds at that spot\n"
    "(decimals work, max 600). The marker itself is never typed.\n"
    "Turn this off to type markers as normal text.",
    kind="muted",
).grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))

# --- Difficulty pauses (Update #33) ---
modern_checkbox(
    frame_difficulty_pauses, "Enable difficulty-aware thinking pauses", enable_difficulty_pauses_var
).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

modern_label(frame_difficulty_pauses, "Pause Strength (%):").grid(row=1, column=0, sticky="w", padx=(0, 8))
entry_difficulty_pause_strength = modern_entry(frame_difficulty_pauses, difficulty_pause_strength_var, width=8)
entry_difficulty_pause_strength.grid(row=1, column=1, sticky="w")

modern_label(
    frame_difficulty_pauses,
    "Real people slow down on hard material. Amanuensis stops for a moment before "
    "a hard word (long, many syllables, numbers, or an acronym) and for a little "
    "longer before a very long or packed sentence. It does not stop for every hard "
    "word, and never twice in a row.\n\n"
    "100% is the normal length. Use less for shorter pauses or more for longer ones "
    "(0 to 300). Protected text never gets a pause, and the time estimate includes "
    "these pauses.",
    kind="muted",
    wraplength=420,
).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

# --- Formatting markers (Update #29) ---
modern_checkbox(
    frame_format_markers, "Press bold and italic shortcuts for formatting markers", enable_format_markers_var
).grid(row=0, column=0, sticky="w")
modern_label(
    frame_format_markers,
    "Wrap text in [[b]]bold[[/b]] or [[i]]italic[[/i]] and Amanuensis presses "
    "Ctrl+B or Ctrl+I at those spots, so it comes out formatted in Google Docs "
    "or Word. The markers themselves are never typed. You can use both at once.\n\n"
    "Turn this off to remove the markers without pressing any shortcut (good for "
    "plain web forms, where Ctrl+B may do something else).\n\n"
    "[[b]], [[/b]], [[i]], and [[/i]] are reserved, so protected text cannot be "
    "exactly b or i. Reworks never delete the first formatted word, and the "
    "sentence drafts are skipped while a style is on. When resuming, click right "
    "after the last typed character so the formatting carries on.",
    kind="muted",
    wraplength=420,
).grid(row=1, column=0, sticky="w", pady=(6, 0))

# --- Character typos ---
modern_checkbox(frame_typos, "Enable adjacent-key typos", enable_typos_var).grid(
    row=0, column=0, columnspan=2, sticky="w"
)
modern_label(frame_typos, "Chance per letter (%)").grid(row=1, column=0, sticky="w", pady=(6, 0))
entry_typo_chance = modern_entry(frame_typos, typo_chance_var)
entry_typo_chance.grid(row=2, column=0, sticky="w")
modern_label(
    frame_typos,
    "A wrong, physically nearby key gets typed, paused on,\nthen corrected -- like a real everyday typo.",
    kind="muted",
).grid(row=1, column=1, rowspan=2, sticky="w", padx=(16, 0))

modern_checkbox(
    frame_typos, "Use extra typo types", use_extra_typos_var
).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
modern_label(
    frame_typos,
    "Adds swapped letters ('teh'), doubled letters, and missed spaces,\n"
    "noticed late and corrected after 1-3 extra characters (shares typo chance).",
    kind="muted",
).grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))

# --- Mistake & break frequency ---
modern_checkbox(
    frame_mistakes,
    "Enable sentence reworks (pause, delete last few words, retype)",
    enable_mistakes_var,
).grid(
    row=0, column=0, columnspan=2, sticky="w"
)
modern_label(frame_mistakes, "Min words").grid(row=1, column=0, sticky="w", pady=(6, 0))
modern_label(frame_mistakes, "Max words").grid(row=1, column=1, sticky="w", padx=(16, 0), pady=(6, 0))
entry_mistake_min = modern_entry(frame_mistakes, mistake_min_var)
entry_mistake_min.grid(row=2, column=0, sticky="w")
entry_mistake_max = modern_entry(frame_mistakes, mistake_max_var)
entry_mistake_max.grid(row=2, column=1, sticky="w", padx=(16, 0))

modern_separator(frame_mistakes).grid(row=3, column=0, columnspan=2, sticky="we", pady=10)

modern_checkbox(frame_mistakes, "Enable research breaks", enable_breaks_var).grid(
    row=4, column=0, columnspan=2, sticky="w"
)
modern_label(frame_mistakes, "Min paragraphs").grid(row=5, column=0, sticky="w", pady=(6, 0))
modern_label(frame_mistakes, "Max paragraphs").grid(row=5, column=1, sticky="w", padx=(16, 0), pady=(6, 0))
entry_break_min = modern_entry(frame_mistakes, break_min_var)
entry_break_min.grid(row=6, column=0, sticky="w")
entry_break_max = modern_entry(frame_mistakes, break_max_var)
entry_break_max.grid(row=6, column=1, sticky="w", padx=(16, 0))

modern_label(frame_mistakes, "Min duration (s)").grid(row=7, column=0, sticky="w", pady=(6, 0))
modern_label(frame_mistakes, "Max duration (s)").grid(row=7, column=1, sticky="w", padx=(16, 0), pady=(6, 0))
entry_break_duration_min = modern_entry(frame_mistakes, break_duration_min_var)
entry_break_duration_min.grid(row=8, column=0, sticky="w")
entry_break_duration_max = modern_entry(frame_mistakes, break_duration_max_var)
entry_break_duration_max.grid(row=8, column=1, sticky="w", padx=(16, 0))

modern_separator(frame_mistakes).grid(row=9, column=0, columnspan=2, sticky="we", pady=10)

modern_checkbox(
    frame_mistakes,
    "Enable paragraph-ending paraphrase & correct",
    enable_paraphrase_var,
).grid(row=10, column=0, columnspan=2, sticky="w")
modern_label(frame_mistakes, "Chance (%)").grid(row=11, column=0, sticky="w", pady=(6, 0))
entry_paraphrase_chance = modern_entry(frame_mistakes, paraphrase_chance_var)
entry_paraphrase_chance.grid(row=12, column=0, sticky="w")
modern_label(
    frame_mistakes,
    "Drafts an alternative sentence at paragraph ends,\n"
    "pauses, deletes it, then writes the real text.\n"
    "(Uses simple word substitution, not AI rewriting.)",
    kind="muted",
).grid(row=11, column=1, rowspan=2, sticky="w", padx=(16, 0))

# --- Smart text fixes (Update #22) ---
modern_checkbox(frame_smart_fixes, "Enable smart text fixes", enable_smart_fixes_var).grid(
    row=0, column=0, columnspan=2, sticky="w"
)
modern_label(
    frame_smart_fixes,
    "Fixes common slips in your text before it is typed, using simple\n"
    "built-in rules. Everything stays on your computer: no AI, no internet.",
    kind="muted",
).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

modern_separator(frame_smart_fixes).grid(row=2, column=0, columnspan=2, sticky="we", pady=10)

modern_checkbox(
    frame_smart_fixes, "Spacing (extra, trailing, and missing spaces)", smart_fix_spacing_var
).grid(row=3, column=0, columnspan=2, sticky="w")
modern_checkbox(
    frame_smart_fixes,
    "Punctuation (missing commas and end periods, doubled marks)",
    smart_fix_punctuation_var,
).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
modern_checkbox(
    frame_smart_fixes, "Capitalization (sentence starts and the word I)", smart_fix_capitalization_var
).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
modern_checkbox(
    frame_smart_fixes, "Repeated words (\"the the\")", smart_fix_repeated_words_var
).grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

modern_separator(frame_smart_fixes).grid(row=7, column=0, columnspan=2, sticky="we", pady=10)

modern_label(frame_smart_fixes, "Show fixes being noticed (%)").grid(row=8, column=0, sticky="w")
entry_smart_fix_visible_chance = modern_entry(frame_smart_fixes, smart_fix_visible_chance_var)
entry_smart_fix_visible_chance.grid(row=9, column=0, sticky="w")
modern_label(
    frame_smart_fixes,
    "That share of fixes is acted out: a quick slip, a pause,\n"
    "then a backspace. The final text is the same either way.",
    kind="muted",
).grid(row=8, column=1, rowspan=2, sticky="w", padx=(16, 0))

modern_button(
    frame_smart_fixes, "Review Fixes For This File...", open_smart_fix_review, kind="neutral"
).grid(row=10, column=0, columnspan=2, sticky="w", pady=(12, 0))
modern_label(
    frame_smart_fixes,
    "Text inside [[double brackets]] is never changed. English rules only.\n"
    "Web addresses, emails, file paths, and code are left alone.",
    kind="muted",
).grid(row=11, column=0, columnspan=2, sticky="w", pady=(8, 0))

# --- Leave mistakes for me (Update #24) ---
modern_checkbox(
    frame_leave_mistakes, "Leave some mistakes for me to fix", enable_leave_mistakes_var
).grid(row=0, column=0, columnspan=2, sticky="w")
modern_label(
    frame_leave_mistakes,
    "Purposely leaves a few mistakes in the typed text so you can\n"
    "find and fix them yourself. When the run ends, a list shows\n"
    "exactly where each one is and what it should be.",
    kind="muted",
).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

modern_separator(frame_leave_mistakes).grid(row=2, column=0, columnspan=2, sticky="we", pady=10)

modern_label(frame_leave_mistakes, "Mistakes to leave (per file)").grid(row=3, column=0, sticky="w")
entry_leave_mistake_count = modern_entry(frame_leave_mistakes, leave_mistake_count_var)
entry_leave_mistake_count.grid(row=4, column=0, sticky="w")
modern_label(
    frame_leave_mistakes,
    "Spread out across the file. If the text has too few\n"
    "safe spots, fewer are left.",
    kind="muted",
).grid(row=3, column=1, rowspan=2, sticky="w", padx=(16, 0))

modern_separator(frame_leave_mistakes).grid(row=5, column=0, columnspan=2, sticky="we", pady=10)

modern_checkbox(
    frame_leave_mistakes, "Typos (letters and joined words)", leave_mistakes_typos_var
).grid(row=6, column=0, columnspan=2, sticky="w")
modern_checkbox(frame_leave_mistakes, "Missing commas", leave_mistakes_commas_var).grid(
    row=7, column=0, columnspan=2, sticky="w", pady=(8, 0)
)
modern_checkbox(
    frame_leave_mistakes,
    "Missing punctuation (periods, ? ! and apostrophes)",
    leave_mistakes_punctuation_var,
).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))

modern_separator(frame_leave_mistakes).grid(row=9, column=0, columnspan=2, sticky="we", pady=10)

modern_button(
    frame_leave_mistakes, "View Last Mistake List", open_left_mistake_report, kind="neutral"
).grid(row=10, column=0, columnspan=2, sticky="w")
modern_label(
    frame_leave_mistakes,
    "Text inside [[double brackets]] is never changed. The typer never\n"
    "fixes a left mistake by accident. Smart Text Fixes run first.",
    kind="muted",
).grid(row=11, column=0, columnspan=2, sticky="w", pady=(8, 0))

# --- Hotkeys ---
entry_hotkey_start = modern_entry(frame_hotkeys, hotkey_start_var)
entry_hotkey_pause = modern_entry(frame_hotkeys, hotkey_pause_var)
entry_hotkey_stop = modern_entry(frame_hotkeys, hotkey_stop_var)

modern_label(frame_hotkeys, "Start").grid(row=0, column=0, sticky="w")
modern_label(frame_hotkeys, "Pause").grid(row=0, column=1, sticky="w", padx=(16, 0))
modern_label(frame_hotkeys, "Stop").grid(row=0, column=2, sticky="w", padx=(16, 0))
entry_hotkey_start.grid(row=1, column=0, sticky="w", pady=(2, 0))
entry_hotkey_pause.grid(row=1, column=1, sticky="w", padx=(16, 0), pady=(2, 0))
entry_hotkey_stop.grid(row=1, column=2, sticky="w", padx=(16, 0), pady=(2, 0))

modern_button(frame_hotkeys, "Apply Hotkeys", apply_hotkeys).grid(
    row=2, column=0, columnspan=3, pady=(12, 0), sticky="w"
)
modern_label(frame_hotkeys, textvariable=hotkey_warning_var, kind="warning").grid(
    row=3, column=0, columnspan=3, pady=(6, 0), sticky="w"
)

# Rebind only on focus-out (debounced) rather than on every keystroke
entry_hotkey_start.bind("<FocusOut>", apply_hotkeys)
entry_hotkey_pause.bind("<FocusOut>", apply_hotkeys)
entry_hotkey_stop.bind("<FocusOut>", apply_hotkeys)

# --- Profiles ---
modern_button(frame_profiles, "Save Profile...", save_profile, kind="neutral").grid(row=0, column=0, padx=(0, 8))
modern_button(frame_profiles, "Load Profile...", load_profile, kind="neutral").grid(row=0, column=1, padx=(0, 8))
modern_button(frame_profiles, "Reset All Settings", reset_to_defaults, kind="danger").grid(row=0, column=2)
modern_label(
    frame_profiles,
    "Your settings are also remembered automatically between launches --\nuse these only if you want named presets to switch between.",
    kind="muted",
).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

# --- Logging ---
modern_checkbox(
    frame_logging,
    "Enable session log (auto-saved as CSV when a run ends)",
    log_enabled_var,
).grid(row=0, column=0, columnspan=2, sticky="w")
modern_button(frame_logging, "Export Log Now", lambda: export_log(auto=False), kind="neutral").grid(
    row=1, column=0, sticky="w", pady=(10, 0), padx=(0, 8)
)
modern_button(frame_logging, "View Last Session Log", open_log_viewer, kind="neutral").grid(
    row=1, column=1, sticky="w", pady=(10, 0)
)

# --- On Completion ---
modern_checkbox(
    frame_completion, "Show notification when finished", enable_notify_var
).grid(row=0, column=0, sticky="w")
modern_checkbox(
    frame_completion, "Play sound when finished", enable_sound_var
).grid(row=1, column=0, sticky="w", pady=(8, 0))
if not HAS_WINSOUND:
    modern_label(
        frame_completion,
        "winsound not available on this platform \u2014 sound disabled.",
        kind="warning",
    ).grid(row=2, column=0, sticky="w", pady=(6, 0))
modern_label(
    frame_completion,
    "Both only trigger when a run finishes naturally, not on a manual Stop.",
    kind="muted",
).grid(row=3, column=0, sticky="w", pady=(6, 0))

# --- Updates (Update #25) ---
modern_checkbox(
    frame_updates, "Check for updates when the app starts", enable_update_check_var
).grid(row=0, column=0, sticky="w")
modern_label(
    frame_updates,
    "Asks GitHub for the newest version number. Nothing from your\n"
    "text or settings is ever sent. Turn this off to keep the app\n"
    "fully offline.",
    kind="muted",
).grid(row=1, column=0, sticky="w", pady=(4, 0))
modern_separator(frame_updates).grid(row=2, column=0, sticky="we", pady=10)
modern_button(
    frame_updates, "Check for Updates Now", check_for_updates_now, kind="neutral"
).grid(row=3, column=0, sticky="w")
modern_label(
    frame_updates, textvariable=update_status_var, kind="muted", wraplength=480
).grid(row=4, column=0, sticky="w", pady=(8, 0))

# --- Version History (Idea #23) ---
# Read-only. Nothing here is a setting, so it is not part of the settings
# dict, the snapshots, or the Reset This Section list.
HISTORY_TEXT_WRAP = 500
# How many releases the tab shows in total. The newest one gets the Current
# Version card, so the Past Releases card shows the (this number - 1) before it.
HISTORY_VERSIONS_SHOWN = 10


def format_release_heading(entry):
    """One line like 'v4.2.0  |  2026-10-09  |  Version History tab'."""
    date_text = entry["date"] if entry["date"] else "date not recorded"
    return f"v{entry['version']}  |  {date_text}  |  {entry['title']}"


def format_release_changes(entry):
    """The change lines of one release as bullet-style text."""
    return "\n".join(f"\u2022 {line}" for line in entry["changes"])


_latest_release = VERSION_HISTORY[0]
modern_label(frame_current_version, f"Amanuensis v{APP_VERSION}", kind="stat").grid(
    row=0, column=0, sticky="w"
)
modern_label(
    frame_current_version,
    format_release_heading(_latest_release).split("  |  ", 1)[1],
    kind="muted",
    wraplength=HISTORY_TEXT_WRAP,
).grid(row=1, column=0, sticky="w", pady=(2, 0))
modern_label(
    frame_current_version,
    format_release_changes(_latest_release),
    wraplength=HISTORY_TEXT_WRAP,
).grid(row=2, column=0, sticky="w", pady=(8, 0))

frame_release_list.columnconfigure(0, weight=1)
_history_row = 0
_past_releases = VERSION_HISTORY[1:HISTORY_VERSIONS_SHOWN]
for _release_index, _release in enumerate(_past_releases):
    if _release_index > 0:
        modern_separator(frame_release_list).grid(row=_history_row, column=0, sticky="we", pady=10)
        _history_row += 1
    modern_label(
        frame_release_list,
        format_release_heading(_release),
        kind="status",
        wraplength=HISTORY_TEXT_WRAP,
    ).grid(row=_history_row, column=0, sticky="w")
    _history_row += 1
    modern_label(
        frame_release_list,
        format_release_changes(_release),
        wraplength=HISTORY_TEXT_WRAP,
    ).grid(row=_history_row, column=0, sticky="w", pady=(4, 0))
    _history_row += 1

# --- Per-section reset buttons (Idea #19) ---
# Each button is added in the first free row at the bottom of its card,
# after the card has been fully built above.
SECTION_CARDS = {
    "Auto Profile": frame_auto_profile,
    "Activity Auto-Pause": frame_activity,
    "Typing Speed": frame_settings,
    "Target Time": frame_target_time,
    "Fatigue Drift": frame_fatigue,
    "Finger & Hand Timing": frame_finger_timing,
    "Flow & Hesitation": frame_typing_moods,
    "Natural Pauses": frame_pauses,
    "Difficulty Pauses": frame_difficulty_pauses,
    "Formatting Markers": frame_format_markers,
    "Character Typos": frame_typos,
    "Sentence Reworks & Breaks": frame_mistakes,
    "Smart Text Fixes": frame_smart_fixes,
    "Leave Mistakes For Me": frame_leave_mistakes,
    "Hotkeys": frame_hotkeys,
    "Session Log": frame_logging,
    "On Completion": frame_completion,
    "Updates": frame_updates,
}


def add_section_reset_button(section_name, card):
    """Adds a 'Reset This Section' button in the next free grid row at
    the bottom-right of a card."""
    columns_used, next_row = card.grid_size()
    # An extra stretchy column pushes the button to the card's right edge.
    card.columnconfigure(columns_used, weight=1)
    reset_button = modern_button(
        card, "Reset This Section", lambda name=section_name: reset_section(name), kind="danger"
    )
    reset_button.grid(row=next_row, column=0, columnspan=columns_used + 1, sticky="e", pady=(10, 0))


for _section_name, _section_card in SECTION_CARDS.items():
    add_section_reset_button(_section_name, _section_card)

# Size the window to fit the finished interface (Idea #21), then restore
# last-used settings and start the periodic GUI refresh + queue-draining
# loops. Auto-save happens on close via on_close(), registered below.
calculate_window_sizes()
load_last_settings()
input_path_var.trace_add("write", lambda *args: refresh_resume_status())
file_queue_var.trace_add("write", lambda *args: refresh_resume_status())
refresh_resume_status()
apply_hotkeys()
window.protocol("WM_DELETE_WINDOW", on_close)
window.after(UPDATE_STARTUP_DELAY_MS, run_startup_update_check)
window.after(500, update_gui)
window.after(50, process_gui_queue)
window.mainloop()
