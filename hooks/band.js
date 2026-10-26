// The band above the prompt, shared by several mods: one row of buttons, and under it
// a list of info lines. Each mod's render hook gets the band drawn by the mods beneath
// it (`previous`); when that band follows this layout, its buttons and lines are taken
// over, so every button stays in one row however long a line gets. The same file ships
// in each mod that draws there (tasklist, bi-dash, workers); keep the copies in step.

const MARK = 'mods-band';

const keyOf = n => String(n?.props?.key ?? '');
// A drawn element as `next(e)` hands it back: its children sit beside its props.
const childrenOf = n => {
  const c = n?.children ?? n?.props?.children;
  return Array.isArray(c) ? c : c == null ? [] : [c];
};

/** The buttons and lines of a band drawn in this layout; anything else is one button-row item. */
export function bandParts(previous) {
  if (previous == null || previous === false) return { buttons: [], lines: [] };
  if (previous.type === 'Box' && keyOf(previous).endsWith(MARK)) {
    const kids = childrenOf(previous);
    const row = kids.find(k => keyOf(k).endsWith(`${MARK}-buttons`));
    const list = kids.find(k => keyOf(k).endsWith(`${MARK}-lines`));
    return { buttons: childrenOf(row), lines: childrenOf(list) };
  }
  return { buttons: [previous], lines: [] };
}

/** The band with this mod's buttons added to the row and its lines added to the list. */
export function band(Box, previous, buttons, lines) {
  const prior = bandParts(previous);
  const allLines = [...prior.lines, ...lines.filter(Boolean)];
  return Box({
    key: MARK,
    flexDirection: 'column',
    rowGap: 1,
    children: [
      Box({ key: `${MARK}-buttons`, flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', columnGap: 1, rowGap: 1, children: [...prior.buttons, ...buttons] }),
      ...(allLines.length ? [Box({ key: `${MARK}-lines`, flexDirection: 'column', children: allLines })] : []),
    ],
  });
}
