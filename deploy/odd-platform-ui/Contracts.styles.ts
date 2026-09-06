import styled, { css } from 'styled-components';

export const Grid = styled.div(
  ({ theme }) => css`
    display: grid;
    grid-template-columns: 2fr 1fr 1fr 1fr 1fr;
    align-items: center;
    gap: ${theme.spacing(1)};
    padding: ${theme.spacing(1)} ${theme.spacing(1.5)};
    border-bottom: 1px solid ${theme.palette.border.primary};

    &:last-of-type {
      border-bottom: none;
    }
  `
);

export const HeaderRow = styled(Grid)(
  ({ theme }) => css`
    color: ${theme.palette.texts.secondary};
    border-bottom: 1px solid ${theme.palette.border.primary};
  `
);

export const Row = styled(Grid)<{ $selected?: boolean }>(
  ({ theme, $selected }) => css`
    cursor: pointer;
    background: ${$selected ? theme.palette.backgrounds.secondary : 'transparent'};

    &:hover {
      background: ${theme.palette.backgrounds.primary};
    }
  `
);

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
    overflow-x: auto;
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

export const Actions = styled.div(
  ({ theme }) => css`
    display: flex;
    align-items: flex-end;
    gap: ${theme.spacing(1)};
    flex-wrap: wrap;
  `
);
