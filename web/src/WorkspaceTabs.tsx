import { useRef } from "react";

export type DatasetTab = "understanding" | "preview" | "profile";

const tabs: ReadonlyArray<{ id: DatasetTab; label: string }> = [
  { id: "understanding", label: "데이터 이해" },
  { id: "preview", label: "데이터 조회" },
  { id: "profile", label: "탐색" },
];

/** 페이지 내 Dataset 작업만 담당한다. 전역 Jobs/Storage는 별도 메뉴다. */
export function WorkspaceTabs({
  active, onChange,
}: { active: DatasetTab; onChange: (tab: DatasetTab) => void }) {
  const tablistRef = useRef<HTMLDivElement>(null);
  return <div className="tabbar" ref={tablistRef} role="tablist" aria-label="데이터 작업 영역">
    {tabs.map(({ id, label }, index) => <button
      key={id}
      type="button"
      role="tab"
      id={`sos-tab-${id}`}
      aria-controls={active === id ? `sos-panel-${id}` : undefined}
      aria-selected={active === id}
      tabIndex={active === id ? 0 : -1}
      className={active === id ? "tab active" : "tab"}
      onClick={() => onChange(id)}
      onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End"
          ? tabs.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        onChange(tabs[next].id);
        requestAnimationFrame(() => {
          tablistRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
        });
      }}
    >{label}</button>)}
  </div>;
}
