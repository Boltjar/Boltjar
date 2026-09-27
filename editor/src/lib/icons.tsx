// ============================================================================
// Icons: react-icons `io5` set (which IS Ionicons 7). No CDN <ion-icon> web
// components. We keep the kebab-case Ionicon names as the lookup key and
// resolve each to its io5 React component, so every call site names an icon
// the way Ionicons does.
// ============================================================================
import type { IconBaseProps } from "react-icons";
import {
  IoTimerOutline,
  IoCalendarOutline,
  IoRadioOutline,
  IoPlayCircleOutline,
  IoPowerOutline,
  IoSparklesOutline,
  IoDocumentTextOutline,
  IoCreateOutline,
  IoFolderOpenOutline,
  IoMicOutline,
  IoVolumeHighOutline,
  IoAlbumsOutline,
  IoGitBranchOutline,
  IoGitMergeOutline,
  IoCalculatorOutline,
  IoGlobeOutline,
  IoTvOutline,
  IoGitCompareOutline,
  IoServerOutline,
  IoGridOutline,
  IoExitOutline,
  IoCubeOutline,
  IoFlash,
  IoFlashOutline,
  IoPlay,
  IoPause,
  IoStop,
  IoRefreshOutline,
  IoAdd,
  IoAddCircleOutline,
  IoSearchOutline,
  IoSaveOutline,
  IoArrowUndoOutline,
  IoArrowRedoOutline,
  IoCopyOutline,
  IoTrashOutline,
  IoScanOutline,
  IoAddOutline,
  IoRemoveOutline,
  IoHandLeftOutline,
  IoGitNetworkOutline,
  IoSettingsOutline,
  IoLockClosedOutline,
  IoPauseCircleOutline,
  IoTerminalOutline,
  IoEllipsisHorizontal,
  IoChevronDownOutline,
  IoChevronForwardOutline,
  IoChevronBackOutline,
  IoEllipse,
  IoEllipseOutline,
  IoSyncOutline,
  IoCheckmarkCircle,
  IoAlertCircleOutline,
  IoCloseCircle,
  IoCloudOfflineOutline,
  IoInformationCircleOutline,
  IoSwapVerticalOutline,
  IoCheckmark,
  IoArrowForwardOutline,
  IoPulseOutline,
  IoLayersOutline,
  IoGitCommitOutline,
  IoHardwareChipOutline,
  IoOptionsOutline,
  IoDocumentOutline,
  IoReorderTwoOutline,
  IoPencil,
  IoEyeOffOutline,
  IoEyeOutline,
  IoCloseOutline,
  IoConstructOutline,
  IoCodeSlashOutline,
  IoToggleOutline,
  IoTextOutline,
  IoColorPaletteOutline,
  IoPower,
  IoChatbubbleEllipsesOutline,
  IoTimeOutline,
  IoPaperPlaneOutline,
  IoSend,
  IoDuplicateOutline,
  IoBanOutline,
  IoCheckmarkDoneOutline,
  IoWarningOutline,
  IoBulbOutline,
  IoClipboardOutline,
  IoEnterOutline,
  IoFunnelOutline,
  IoSparkles,
  IoImageOutline,
  IoVideocamOutline,
  IoChatbubblesOutline,
  IoHelpCircleOutline,
  IoBookOutline,
  IoBugOutline,
  IoHeartOutline,
} from "react-icons/io5";
import type { ComponentType } from "react";

type IconCmp = ComponentType<IconBaseProps>;

/** Ionicon kebab-name -> io5 component. */
const REGISTRY: Record<string, IconCmp> = {
  // node kinds
  flash: IoFlash,
  "flash-outline": IoFlashOutline,
  "git-compare-outline": IoGitCompareOutline,
  "server-outline": IoServerOutline,
  "grid-outline": IoGridOutline,
  "exit-outline": IoExitOutline,
  "cube-outline": IoCubeOutline,
  // specific node types
  "timer-outline": IoTimerOutline,
  "calendar-outline": IoCalendarOutline,
  "radio-outline": IoRadioOutline,
  "play-circle-outline": IoPlayCircleOutline,
  "sparkles-outline": IoSparklesOutline,
  "document-text-outline": IoDocumentTextOutline,
  "create-outline": IoCreateOutline,
  "folder-open-outline": IoFolderOpenOutline,
  "mic-outline": IoMicOutline,
  "volume-high-outline": IoVolumeHighOutline,
  "albums-outline": IoAlbumsOutline,
  "git-branch-outline": IoGitBranchOutline,
  "git-merge-outline": IoGitMergeOutline,
  "calculator-outline": IoCalculatorOutline,
  "globe-outline": IoGlobeOutline,
  "tv-outline": IoTvOutline,
  "construct-outline": IoConstructOutline,
  "code-slash-outline": IoCodeSlashOutline,
  "toggle-outline": IoToggleOutline,
  "text-outline": IoTextOutline,
  "color-palette-outline": IoColorPaletteOutline,
  "chatbubble-ellipses-outline": IoChatbubbleEllipsesOutline,
  "time-outline": IoTimeOutline,
  "paper-plane-outline": IoPaperPlaneOutline,
  // actions
  play: IoPlay,
  pause: IoPause,
  stop: IoStop,
  "refresh-outline": IoRefreshOutline,
  add: IoAdd,
  "add-circle-outline": IoAddCircleOutline,
  "search-outline": IoSearchOutline,
  "save-outline": IoSaveOutline,
  "arrow-undo-outline": IoArrowUndoOutline,
  "arrow-redo-outline": IoArrowRedoOutline,
  "copy-outline": IoCopyOutline,
  "trash-outline": IoTrashOutline,
  "scan-outline": IoScanOutline,
  "add-outline": IoAddOutline,
  "remove-outline": IoRemoveOutline,
  "hand-left-outline": IoHandLeftOutline,
  "git-network-outline": IoGitNetworkOutline,
  "settings-outline": IoSettingsOutline,
  "lock-closed-outline": IoLockClosedOutline,
  "pause-circle-outline": IoPauseCircleOutline,
  "terminal-outline": IoTerminalOutline,
  "ellipsis-horizontal": IoEllipsisHorizontal,
  "chevron-down-outline": IoChevronDownOutline,
  "chevron-forward-outline": IoChevronForwardOutline,
  "chevron-back-outline": IoChevronBackOutline,
  // the design doc's chevron-expand has no io5 twin; the vertical swap glyph is
  // the canonical "expandable dropdown" affordance and matches the look.
  "chevron-expand-outline": IoSwapVerticalOutline,
  "reorder-two-outline": IoReorderTwoOutline,
  pencil: IoPencil,
  "eye-off-outline": IoEyeOffOutline,
  "eye-outline": IoEyeOutline,
  "close-outline": IoCloseOutline,
  // status / health
  ellipse: IoEllipse,
  "ellipse-outline": IoEllipseOutline,
  "sync-outline": IoSyncOutline,
  "checkmark-circle": IoCheckmarkCircle,
  "alert-circle-outline": IoAlertCircleOutline,
  "close-circle": IoCloseCircle,
  "cloud-offline-outline": IoCloudOfflineOutline,
  "information-circle-outline": IoInformationCircleOutline,
  checkmark: IoCheckmark,
  "arrow-forward-outline": IoArrowForwardOutline,
  // telemetry / misc
  "pulse-outline": IoPulseOutline,
  "layers-outline": IoLayersOutline,
  "git-commit-outline": IoGitCommitOutline,
  "hardware-chip-outline": IoHardwareChipOutline,
  "options-outline": IoOptionsOutline,
  "document-outline": IoDocumentOutline,
  // power model
  power: IoPower,
  "power-outline": IoPowerOutline,
  // context menus / actions
  send: IoSend,
  "duplicate-outline": IoDuplicateOutline,
  "ban-outline": IoBanOutline,
  "checkmark-done-outline": IoCheckmarkDoneOutline,
  "warning-outline": IoWarningOutline,
  "bulb-outline": IoBulbOutline,
  "clipboard-outline": IoClipboardOutline,
  // model picker / param promotion
  "enter-outline": IoEnterOutline,
  "funnel-outline": IoFunnelOutline,
  sparkles: IoSparkles,
  "image-outline": IoImageOutline,
  "videocam-outline": IoVideocamOutline,
  "chatbubbles-outline": IoChatbubblesOutline,
  // help menu
  "help-circle-outline": IoHelpCircleOutline,
  "book-outline": IoBookOutline,
  "bug-outline": IoBugOutline,
  "heart-outline": IoHeartOutline,
};

// A purpose-built close (×) icon with NO internal padding, drawn on a perfect
// 12x12 grid centered on (6,6). The Ionicon close-outline ships extra padding
// inside its viewBox which leaves the glyph visually off-center on small (~12px)
// targets like tab/close buttons, so register a "wf-close" name that uses the
// glyph we control, and route close-outline to the same component so existing
// callers benefit automatically.
function WfClose(props: IconBaseProps) {
  const { size = "1em", color, style, className, title } = props as IconBaseProps & { title?: string };
  return (
    <svg
      viewBox="0 0 12 12"
      width={size as number | string}
      height={size as number | string}
      className={className}
      style={style}
      role={title ? "img" : "presentation"}
      aria-label={title}
      focusable={false}
    >
      <path
        d="M 3 3 L 9 9 M 3 9 L 9 3"
        stroke={color ?? "currentColor"}
        strokeWidth={1.6}
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

REGISTRY["wf-close"] = WfClose;
// Route the legacy name to the new glyph so every existing close button
// re-centers automatically (tabs, rail close, popovers, confirms).
REGISTRY["close-outline"] = WfClose;

// Plus / Minus / Fit on the same 12x12 grid, centered on (6,6), 1.6 stroke.
// The Ionicon add/remove/scan glyphs have asymmetric padding and looked
// crooked in the zoom controls; this set sits dead center every time.
function WfPlus(props: IconBaseProps) {
  const { size = "1em", color, style, className } = props as IconBaseProps;
  return (
    <svg viewBox="0 0 12 12" width={size as number | string} height={size as number | string} className={className} style={style} focusable={false} aria-hidden>
      <path d="M 6 2 L 6 10 M 2 6 L 10 6" stroke={color ?? "currentColor"} strokeWidth={1.6} strokeLinecap="round" fill="none" />
    </svg>
  );
}
function WfMinus(props: IconBaseProps) {
  const { size = "1em", color, style, className } = props as IconBaseProps;
  return (
    <svg viewBox="0 0 12 12" width={size as number | string} height={size as number | string} className={className} style={style} focusable={false} aria-hidden>
      <path d="M 2 6 L 10 6" stroke={color ?? "currentColor"} strokeWidth={1.6} strokeLinecap="round" fill="none" />
    </svg>
  );
}
function WfFit(props: IconBaseProps) {
  const { size = "1em", color, style, className } = props as IconBaseProps;
  return (
    <svg viewBox="0 0 12 12" width={size as number | string} height={size as number | string} className={className} style={style} focusable={false} aria-hidden>
      {/* four L-shaped corner brackets, perfectly symmetric */}
      <path d="M 2 4 L 2 2 L 4 2 M 8 2 L 10 2 L 10 4 M 10 8 L 10 10 L 8 10 M 4 10 L 2 10 L 2 8"
            stroke={color ?? "currentColor"} strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
}
REGISTRY["wf-plus"]  = WfPlus;
REGISTRY["wf-minus"] = WfMinus;
REGISTRY["wf-fit"]   = WfFit;
// Route the legacy names so existing call sites recenter automatically.
REGISTRY["add-outline"]    = WfPlus;
REGISTRY["remove-outline"] = WfMinus;
REGISTRY["scan-outline"]   = WfFit;

export type IconName = string;

interface IconProps extends IconBaseProps {
  name: IconName;
}

/**
 * Render an Ionicon by its kebab-case name. Unknown names degrade to a neutral
 * dot so a missing mapping never throws or leaves a blank; colour inherits
 * `currentColor`, matching the <ion-icon> contract the design system assumes.
 */
export function Icon({ name, ...rest }: IconProps) {
  const Cmp = REGISTRY[name] ?? IoEllipseOutline;
  return <Cmp {...rest} />;
}
