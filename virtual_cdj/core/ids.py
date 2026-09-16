"""Eindeutige IDs aller Bedienelemente.

Namenskonvention
----------------
* Nur ``A-Z``, ``0-9`` und ``_``.
* Gruppenpraefix, wenn ein Element ohne Kontext nicht eindeutig waere
  (``TRACK_SEARCH_PREV`` statt ``PREV``).
* Richtungen: ``_PREV`` / ``_NEXT`` bei Listen, ``_BACK`` / ``_FWD`` bei
  Zeitachsen, ``_UP`` / ``_DOWN`` bei Werten.
* Ein drueckbarer Encoder wird in zwei IDs aufgeteilt:
  ``<NAME>_ROTATE`` und ``<NAME>_PRESS``.
* Das Jogwheel wird in ``JOG_MOVE`` (relativ) und ``JOG_TOUCH`` (digital)
  aufgeteilt.
* ``UNRESOLVED_*`` markiert ein im Bild erkanntes Element, dessen Beschriftung
  nicht sicher lesbar war. Diese IDs werden umbenannt, sobald geklaert ist,
  welches Bedienelement gemeint ist.
"""

# --- Tastenreihe oberhalb des Displays -------------------------------------
SOURCE = "SOURCE"
BROWSE = "BROWSE"
TAG_LIST = "TAG_LIST"
PLAYLIST = "PLAYLIST"
SEARCH = "SEARCH"
MENU = "MENU"
SCREEN_BUTTONS = (SOURCE, BROWSE, TAG_LIST, PLAYLIST, SEARCH, MENU)

# --- Tasten um den Browse-Drehgeber ---------------------------------------
BACK = "BACK"
TAG_TRACK_REMOVE = "TAG_TRACK_REMOVE"
TRACK_FILTER_EDIT = "TRACK_FILTER_EDIT"
SHORTCUT = "SHORTCUT"
BROWSE_BUTTONS = (BACK, TAG_TRACK_REMOVE, TRACK_FILTER_EDIT, SHORTCUT)

# --- Browse-Drehgeber ------------------------------------------------------
# Eigenstaendig gegenueber dem Taster BROWSE oberhalb des Displays.
BROWSE_ROTATE = "BROWSE_ROTATE"
BROWSE_PRESS = "BROWSE_PRESS"

# --- Medien ----------------------------------------------------------------
USB_STOP = "USB_STOP"

# --- Modifikator -----------------------------------------------------------
#: Quadratischer Taster links neben der Pad-Reihe.
SHIFT = "SHIFT"

# --- Slip / Quantize -------------------------------------------------------
SLIP = "SLIP"
QUANTIZE = "QUANTIZE"

# --- Performance Pads ------------------------------------------------------
PAD_A = "PAD_A"
PAD_B = "PAD_B"
PAD_C = "PAD_C"
PAD_D = "PAD_D"
PAD_E = "PAD_E"
PAD_F = "PAD_F"
PAD_G = "PAD_G"
PAD_H = "PAD_H"
PADS = (PAD_A, PAD_B, PAD_C, PAD_D, PAD_E, PAD_F, PAD_G, PAD_H)

# --- Loop ------------------------------------------------------------------
LOOP_IN = "LOOP_IN"
LOOP_OUT = "LOOP_OUT"
RELOOP_EXIT = "RELOOP_EXIT"
BEAT_LOOP_4 = "BEAT_LOOP_4"
BEAT_LOOP_8 = "BEAT_LOOP_8"
BEAT_LOOPS = (BEAT_LOOP_4, BEAT_LOOP_8)

# --- Hotcue / Beat Jump (die beiden Taster in der Loop-Reihe) --------------
# HOT_CUE und BEAT_JUMP sind die beschrifteten Taster bei y 313.
# BEAT_JUMP_PREV / BEAT_JUMP_NEXT sind die rechteckigen Richtungstaster
# unmittelbar oberhalb des Richtungsschalters.
HOT_CUE = "HOT_CUE"
BEAT_JUMP = "BEAT_JUMP"

# --- Cue/Loop Call, Speicher ----------------------------------------------
CUE_LOOP_CALL_PREV = "CUE_LOOP_CALL_PREV"
CUE_LOOP_CALL_NEXT = "CUE_LOOP_CALL_NEXT"
DELETE = "DELETE"
MEMORY = "MEMORY"

# --- Beat Jump -------------------------------------------------------------
BEAT_JUMP_PREV = "BEAT_JUMP_PREV"
BEAT_JUMP_NEXT = "BEAT_JUMP_NEXT"

# --- Richtung --------------------------------------------------------------
DIRECTION = "DIRECTION"

# --- Suche / Track ---------------------------------------------------------
# Die geriffelten Taster links unten. Nicht zu verwechseln mit dem Taster
# SEARCH oberhalb des Displays.
TRACK_SEARCH_PREV = "TRACK_SEARCH_PREV"
TRACK_SEARCH_NEXT = "TRACK_SEARCH_NEXT"
SEARCH_BACK = "SEARCH_BACK"
SEARCH_FWD = "SEARCH_FWD"

# --- Transport -------------------------------------------------------------
CUE = "CUE"
PLAY = "PLAY"

# --- Sync / Master ---------------------------------------------------------
BEAT_SYNC = "BEAT_SYNC"
MASTER = "MASTER"
KEY_SYNC = "KEY_SYNC"

# --- Tempo -----------------------------------------------------------------
TEMPO_RANGE = "TEMPO_RANGE"
MASTER_TEMPO = "MASTER_TEMPO"
TEMPO_FADER = "TEMPO_FADER"
TEMPO_RESET = "TEMPO_RESET"

# --- Jogwheel --------------------------------------------------------------
JOG_MOVE = "JOG_MOVE"
JOG_TOUCH = "JOG_TOUCH"
#: Taster rechts neben MEMORY.
JOG_MODE = "JOG_MODE"
#: Drehpotentiometer rechts neben der Pad-Reihe.
VINYL_SPEED_ADJUST = "VINYL_SPEED_ADJUST"
