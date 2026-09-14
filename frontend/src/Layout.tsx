import React from "react";

// Self-contained header/page chrome for the standalone wizard app (mirrors the
// catalogue's Layout so the two apps look like one product).

const s = {
  page: {
    display: "flex",
    flexDirection: "column" as const,
    minHeight: "100vh",
    backgroundColor: "#f8f9fa",
    fontFamily: "Arial, sans-serif",
  },
  header: {
    background: "#000079",
    color: "#fff",
    padding: "12px 28px",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    flexWrap: "wrap" as const,
    gap: "8px",
  },
  headerLeft: { display: "flex", alignItems: "center", gap: "14px" },
  logo: { height: "42px" },
  headerTitle: { fontSize: "15px", fontWeight: 600 as const, letterSpacing: "0.3px" },
};

const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={s.page}>
    <header style={s.header}>
      <div style={s.headerLeft}>
        <img
          src="https://stlxipuhot.blob.core.windows.net/staging/sharedmedia/ai/logos/laif_cmyk_negatif.png?ext=.png"
          alt="Luxembourg AI Factory"
          style={s.logo}
        />
        <span style={s.headerTitle}>AI Assessment Wizard</span>
      </div>
    </header>
    {children}
  </div>
);

export default Layout;
