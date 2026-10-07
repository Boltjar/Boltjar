// ============================================================================
// useAutoGrow: a textarea as tall as its text. Every text surface on a node
// either grows to its content (a node with no saved size, at its natural
// height) or fills the height the node was given (a resized node: the flex
// layout sizes it and the text scrolls). One rule, one implementation, shared
// by the code fields, the secret-aware fields and the Template editor.
//
// Grows up to AUTOGROW_CAP, then the textarea scrolls. Re-fits when the text
// changes and when the textarea's WIDTH changes (a node resized, a font loaded
// re-wraps the text); its own height change never triggers a re-fit.
// ============================================================================
import { useLayoutEffect, type RefObject } from "react";

/** The tallest a growing text surface gets before it scrolls (CSS px). */
export const AUTOGROW_CAP = 320;

/** The border-box height that shows `scrollHeight` of text: the content plus
 *  the borders around it, never more than `cap`. */
export function grownHeight(scrollHeight: number, borders: number, cap: number = AUTOGROW_CAP): number {
  return Math.min(scrollHeight + borders, cap);
}

export function useAutoGrow(
  ref: RefObject<HTMLTextAreaElement | HTMLInputElement | null>,
  on: boolean,
  value: string,
  cap: number = AUTOGROW_CAP,
): void {
  useLayoutEffect(() => {
    const el = ref.current as HTMLTextAreaElement | null;
    if (!el || el.tagName !== "TEXTAREA") return;
    if (!on) {
      // the layout sizes it now (a resized node fills its height)
      el.style.height = "";
      return;
    }
    const fit = () => {
      el.style.height = "auto";
      const borders = el.offsetHeight - el.clientHeight;
      el.style.height = `${grownHeight(el.scrollHeight, borders, cap)}px`;
    };
    fit();
    if (typeof ResizeObserver === "undefined") return;
    let width = el.clientWidth;
    const ro = new ResizeObserver(() => {
      if (el.clientWidth === width) return;
      width = el.clientWidth;
      fit();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, on, value, cap]);
}
