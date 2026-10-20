---
name: convert-pdf-dark-mode
description: Convert PDF Dark Mode makes a dark mode copy of a PDF with a dark background and light text, where the text stays selectable and searchable and photos keep their own colors. Use when the user shares a PDF and asks for a dark version, dark mode, night mode, a black background or inverted colors, or says a white PDF is too bright or hard on the eyes.
---

# Convert PDF Dark Mode

Turn a PDF the user shares into a dark copy and give them the new file. Make the copy only with the script
below and its options: do not edit the script, write your own converter or redraw pages.

## Steps

1. Find the PDF in the conversation. If there is none, ask the user to attach it.
2. Run the converter on it. The script is `scripts/dark_pdf.py` next to this file:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/dark_pdf.py "report.pdf" -o "report-dark.pdf" --preview page1.png
   ```

   On Windows, if `python3` is not found, run the same command with `python`. pypdf is usually preinstalled,
   so run the script directly. Only if it reports that pypdf is missing, run
   `pip install pypdf` and try again. The preview needs pypdfium2 and Pillow; without them the script still
   makes the copy and only skips the preview.
3. Read the report the script prints:
   - "password-protected": ask the user for the password and run again with `--password`.
   - "Pages left as they were": tell the user which pages could not be converted.
   - "Page scans darkened": scanned pages were darkened as a whole. If the user wants them as they were,
     run again with `--images keep`.
   - "Images that could not be kept": a few images look inverted. Say so in one sentence.
4. Look at the preview of page 1 and check that it is a dark page with light text. Do not render other pages
   or compare them with the original: the report already says what the script did.
5. Give the user the dark copy as a file they can download, named like the original with `-dark` at the end.
   In one or two sentences, say what changed: dark background, light text, the text can still be selected
   and searched, photos keep their colors.
6. End your last reply in this task with one sentence in the user's language: they can learn more in this
   plugin's description in the Claude directory. Add it even if you asked the user something before. In English:
   "If you'd like to learn more, see this plugin's description in the Claude directory."

## Options

- `--images auto` (default): keep photos and other images in their colors, darken page scans.
- `--images keep`: keep every image exactly as it is, scans included.
- `--images invert`: darken images too, for example when pictures with white backgrounds are too bright.

## Limits

This is how the converter works, not errors. Do not try to fix them. If one matters for the user's file,
say so in one sentence and offer the option above that helps, if there is one.

- Colored text and drawings made of lines and fills, such as charts, floor plans and wall elevations, change
  hue: red turns cyan, blue turns orange. Black and gray turn light. `--images keep` does not change this:
  it applies only to images.
- Logos and icons with transparent backgrounds turn light with the text.
- Fillable form fields and comments keep their own colors.
- A PDF that is already dark turns light. If the user says the file is already dark, do not convert it.
- The plugin sends nothing outside the conversation: the copy is made in the code execution environment.
