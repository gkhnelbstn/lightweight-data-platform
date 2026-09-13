import styled, { css } from 'styled-components';

// The contract list, the Replication table and the open-failures rows are
// laid out with this platform's own Table.HeaderContainer / RowContainer /
// Cell (components/shared/elements) rather than a bespoke grid, so this file
// only carries what those do not: the sortable header button, and layout for
// the panels that sit beside those tables.

/** A Table.HeaderContainer cell that sorts instead of just labelling.
 * font-size matches
 * MUI's caption variant so it sits flush with the Typography cells beside it;
 * everything else is a button reset. */
export const SortableHeader = styled.button`
  all: unset;
  cursor: pointer;
  color: inherit;
  font-size: 0.75rem;
  text-align: left;

  &:hover {
    text-decoration: underline;
  }

  &:focus-visible {
    outline: 2px solid currentColor;
    outline-offset: 2px;
  }
`;

export const Panel = styled.div(
  ({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(2)};
    padding: ${theme.spacing(2)};
    margin-top: ${theme.spacing(2)};
    background: ${theme.palette.backgrounds.tertiary};
    border-radius: ${theme.spacing(1)};
  `
);

export const Sql = styled.pre(
  ({ theme }) => css`
    margin: ${theme.spacing(0.5)} 0 0;
    padding: ${theme.spacing(1)};
    /* Wrapped, not scrolled: this panel is a column inside someone else's
       page and a horizontally scrolling rule is a rule nobody reads. */
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 12px;
    background: ${theme.palette.backgrounds.primary};
    border: 1px solid ${theme.palette.border.primary};
    border-radius: ${theme.spacing(0.5)};
  `
);

export const Textarea = styled.textarea(
  ({ theme }) => css`
    width: 100%;
    min-height: 90px;
    padding: ${theme.spacing(1)};
    font-family: monospace;
    font-size: 12px;
    color: ${theme.palette.texts.primary};
    background: ${theme.palette.backgrounds.primary};
    border: 1px solid ${theme.palette.border.primary};
    border-radius: ${theme.spacing(0.5)};
  `
);

export const Scroll = styled.div`
  overflow-x: auto;
`;

export const Cells = styled.table(
  ({ theme }) => css`
    border-collapse: collapse;
    font-size: 12px;

    th,
    td {
      padding: ${theme.spacing(0.5)} ${theme.spacing(1)};
      text-align: left;
      white-space: nowrap;
      border-bottom: 1px solid ${theme.palette.border.primary};
    }

    th {
      color: ${theme.palette.texts.secondary};
      font-weight: 500;
    }
  `
);

/** The labelled facts about one check. Two columns where there is room, one
 * on a narrow window -- LabeledInfoItem lays out its own label/value pair, so
 * this only says how many of them sit side by side. */
export const Facts = styled.div(
  ({ theme }) => css`
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: ${theme.spacing(0.5)} ${theme.spacing(3)};

    /* LabeledInfoItem truncates its value to one line, which is right in the
       narrow sidebar it was built for and wrong here: "Actual custom_sql(...)
       was 5, expected..." is the whole answer and it is the half that gets
       cut. Wrap instead. */
    * {
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
    }
  `
);

/** One square per daily run, oldest first. */
export const Runs = styled.div(
  ({ theme }) => css`
    display: flex;
    flex-wrap: wrap;
    gap: 2px;
    margin-top: ${theme.spacing(0.5)};
  `
);

export const Run = styled.span<{ $status: string }>(
  ({ theme, $status }) => css`
    width: 10px;
    height: 16px;
    border-radius: 2px;
    /* 'error' is grey, not red: a check that could not run is not a check
       that failed (CLAUDE.md invariant 5) and it is out of the score. */
    background: ${$status === 'pass'
      ? theme.palette.success.main
      : $status === 'error'
        ? theme.palette.texts.secondary
        : theme.palette.error.main};
  `
);

export const Actions = styled.div(
  ({ theme }) => css`
    display: flex;
    align-items: flex-end;
    gap: ${theme.spacing(1)};
    flex-wrap: wrap;
  `
);

/** One property (Definitions) or one check (Checks): a compact row rather
 * than a full Table.RowContainer -- there is no second column to align, just
 * a label line and, sometimes, a line of prose under it. */
export const PropertyRow = styled.div(
  ({ theme }) => css`
    padding: ${theme.spacing(0.75)} 0;
    border-bottom: 1px solid ${theme.palette.border.primary};

    &:last-of-type {
      border-bottom: none;
    }
  `
);
