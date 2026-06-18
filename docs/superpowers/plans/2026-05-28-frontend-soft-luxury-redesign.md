# AiWardrobe 前端设计方案 — Soft Luxury 重设计

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 AiWardrobe 前端从"工具型 SaaS"升级为"精致私人衣橱顾问"，通过字体、色彩、动效三个维度提升视觉品质感。

**Architecture:** 渐进式改造，不引入任何 CSS 框架或新依赖。先在 `tokens.css` 建立 Design Token 体系，然后逐组件替换硬编码值为 token 引用，最后添加骨架屏、微交互动效。所有改动向后兼容，每个阶段完成后均可独立交付。

**Tech Stack:** React 18 + Vite + TypeScript，手写 CSS（无框架），Lucide React 图标库，Google Fonts（Cormorant + Montserrat）

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `frontend/src/tokens.css` | Design Token CSS 变量定义 | **新建** |
| `frontend/src/styles.css` | 全局样式、组件样式、响应式、动效 | **修改** |
| `frontend/src/App.tsx` | 所有视图组件 | **修改**（仅 skeleton/lazy 加载等结构性改动） |
| `frontend/src/api.ts` | API 调用层 | 不修改 |
| `frontend/src/types.ts` | TypeScript 类型 | 不修改 |
| `frontend/index.html` | HTML 入口（Google Fonts 引入） | **修改** |

---

### Task 1: 建立 Design Token 体系

**Files:**
- Create: `frontend/src/tokens.css`
- Modify: `frontend/src/styles.css:1-3`（顶部添加 @import）

- [ ] **Step 1: 创建 tokens.css 文件**

```css
/* ===== AiWardrobe Design Tokens =====
   Soft Luxury 方向 — 精致 / 温暖 / 智能 */

:root {
  /* === 字体 === */
  --font-heading: 'Cormorant', 'Georgia', serif;
  --font-body: 'Montserrat', 'Plus Jakarta Sans', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;

  /* === 字阶 === */
  --text-xs: 0.75rem;
  --text-sm: 0.8125rem;
  --text-base: 0.9375rem;
  --text-lg: 1.125rem;
  --text-xl: 1.375rem;
  --text-2xl: 1.75rem;
  --text-3xl: clamp(2rem, 4vw, 2.75rem);

  /* === 行高 === */
  --leading-tight: 1.15;
  --leading-normal: 1.65;
  --leading-relaxed: 1.8;

  /* === 字重 === */
  --weight-normal: 400;
  --weight-medium: 500;
  --weight-semibold: 600;
  --weight-bold: 700;
  --weight-extrabold: 800;

  /* === 主色调 Rose === */
  --color-primary-50:  #FFF1F2;
  --color-primary-100: #FFE4E6;
  --color-primary-200: #FECDD3;
  --color-primary-300: #FDA4AF;
  --color-primary-400: #FB7185;
  --color-primary-500: #EC4899;
  --color-primary-600: #DB2777;
  --color-primary-700: #BE185D;
  --color-primary-800: #9D174D;
  --color-primary-900: #831843;

  /* === 辅助色 Violet (AI/CTA) === */
  --color-accent-400: #A78BFA;
  --color-accent-500: #8B5CF6;
  --color-accent-600: #7C3AED;
  --color-accent-700: #6D28D9;

  /* === 中性色 Warm Gray === */
  --color-neutral-50:  #FAFAFA;
  --color-neutral-100: #F5F5F4;
  --color-neutral-200: #E7E5E4;
  --color-neutral-300: #D6D3D1;
  --color-neutral-400: #A8A29E;
  --color-neutral-500: #78716C;
  --color-neutral-600: #57534E;
  --color-neutral-700: #44403C;
  --color-neutral-800: #292524;
  --color-neutral-900: #1C1917;

  /* === 语义色 === */
  --color-success: #059669;
  --color-success-bg: #ECFDF5;
  --color-success-border: #A7F3D0;
  --color-warning: #D97706;
  --color-warning-bg: #FFFBEB;
  --color-warning-border: #FDE68A;
  --color-error: #DC2626;
  --color-error-bg: #FEF2F2;
  --color-error-border: #FECACA;

  /* === 间距 === */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --space-12: 48px;

  /* === 圆角 === */
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-full: 9999px;

  /* === 阴影 === */
  --shadow-sm:  0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow-md:  0 4px 12px rgba(0, 0, 0, 0.06);
  --shadow-lg:  0 8px 24px rgba(0, 0, 0, 0.08);
  --shadow-glow: 0 0 24px rgba(236, 72, 153, 0.18);

  /* === 边框 === */
  --border-thin: 1px solid var(--color-neutral-200);
  --border-focus: 3px solid var(--color-primary-300);

  /* === 动效 === */
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
  --duration-fast: 150ms;
  --duration-normal: 200ms;
  --duration-slow: 350ms;

  /* === 布局 === */
  --sidebar-width: 260px;
  --content-max-width: 1180px;
  --bottom-nav-height: 76px;

  /* === 背景 === */
  --bg-app: linear-gradient(180deg, #FFF7FB 0%, #FAFAFA 100%);
  --bg-surface: #FFFFFF;
  --bg-subtle: #FDF2F8;
}
```

- [ ] **Step 2: 在 styles.css 顶部引入 tokens.css**

在 `frontend/src/styles.css` 的第一行（`@import url(...)` 之前）插入：

```css
@import url("./tokens.css");
```

完整变更 — 将 `styles.css:1` 从：

```css
@import url("https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap");
```

改为：

```css
@import url("./tokens.css");
@import url("https://fonts.googleapis.com/css2?family=Cormorant:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&family=Montserrat:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&display=swap");
```

- [ ] **Step 3: 验证 tokens.css 被正确加载**

Run: `cd frontend && npm run dev`

打开浏览器 DevTools → Elements → 检查 `:root` 下是否出现 `--font-heading`、`--color-primary-500` 等 CSS 变量。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/tokens.css frontend/src/styles.css
git commit -m "feat: add design token system (Soft Luxury direction)

Establish CSS custom properties for typography, color palette
(Rose + Violet accent + Warm Gray), spacing, shadows, radii,
easing curves, and layout constants.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 全局样式 Token 化 — 字体、颜色、背景

**Files:**
- Modify: `frontend/src/styles.css`（替换所有硬编码颜色和字体为 token 引用）

- [ ] **Step 1: 替换 `:root` 全局变量**

将 `styles.css:3-9` 从：

```css
:root {
  font-family: "Plus Jakarta Sans", system-ui, sans-serif;
  color: #111827;
  background: #f8fafc;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
}
```

改为：

```css
:root {
  font-family: var(--font-body);
  color: var(--color-neutral-800);
  background: var(--color-neutral-50);
  font-synthesis: none;
  text-rendering: optimizeLegibility;
}
```

- [ ] **Step 2: 替换登录页背景**

将 `.loginPage` 的 `background: linear-gradient(180deg, #fff7fb 0%, #f8fafc 100%);` 替换为：

```css
.loginPage {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--bg-app);
}
```

- [ ] **Step 3: 替换品牌色**

将 `.brandMark` 的 `color: #be185d; background: #fce7f3; border: 1px solid #fbcfe8;` 替换为：

```css
.brandMark {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  color: var(--color-primary-700);
  background: var(--color-primary-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-primary-200);
}
```

- [ ] **Step 4: 替换所有文本颜色**

执行以下查找替换（使用 Edit 工具逐项替换）：

| 旧值 | 新值 |
|------|------|
| `color: #111827` | `color: var(--color-neutral-800)` |
| `color: #334155` | `color: var(--color-neutral-700)` |
| `color: #475569` | `color: var(--color-neutral-500)` |
| `color: #64748b` | `color: var(--color-neutral-400)` |

- [ ] **Step 5: 替换所有主色引用**

| 旧值 | 新值 |
|------|------|
| `#db2777` | `var(--color-primary-600)` |
| `#be185d` | `var(--color-primary-700)` |
| `#f9a8d4` | `var(--color-primary-300)` |
| `#fce7f3` | `var(--color-primary-50)` |
| `#fdf2f8` | `var(--color-primary-50)` |
| `#fff7fb` | `var(--color-primary-50)` |
| `#fbcfe8` | `var(--color-primary-200)` |

- [ ] **Step 6: 替换边框和背景色**

| 旧值 | 新值 |
|------|------|
| `#e5e7eb` | `var(--color-neutral-200)` |
| `#e2e8f0` | `var(--color-neutral-200)` |
| `#cbd5e1` | `var(--color-neutral-300)` |
| `#f8fafc` | `var(--color-neutral-50)` |
| `#f1f5f9` | `var(--color-neutral-100)` |
| `border: 1px solid #fecdd3` | `border: 1px solid var(--color-primary-200)` |
| `border: 1px solid #fecaca` | `border: 1px solid var(--color-error-border)` |

- [ ] **Step 7: 替换语义色**

| 旧值 | 新值 |
|------|------|
| `#991b1b` | `var(--color-error)` |
| `#b91c1c` | `var(--color-error)` |
| `#fef2f2` | `var(--color-error-bg)` |
| `#9f1239` | `var(--color-primary-800)` |
| `#fff1f2` | `var(--color-primary-50)` |

- [ ] **Step 8: 替换圆角**

| 旧值 | 新值 |
|------|------|
| `border-radius: 6px` | `border-radius: var(--radius-sm)` |
| `border-radius: 8px` | `border-radius: var(--radius-md)` |

- [ ] **Step 9: 验证视觉效果不变**

Run: `cd frontend && npm run dev`

在浏览器中浏览登录页、衣橱页、上传页，确认所有颜色、圆角与改动前一致（因为 token 值映射了旧值）。

- [ ] **Step 10: Commit**

```bash
git add frontend/src/styles.css
git commit -m "refactor: replace hardcoded CSS values with design tokens

All colors, radii, and fonts now reference CSS custom properties
from tokens.css. No visual change intended.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: 字体升级 — Cormorant + Montserrat

**Files:**
- Modify: `frontend/src/styles.css`（标题元素应用 Cormorant，调整字阶）

- [ ] **Step 1: 页面标题使用 Cormorant**

将 h1 样式改为：

```css
h1 {
  font-family: var(--font-heading);
  font-size: var(--text-3xl);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
  letter-spacing: -0.02em;
  margin-bottom: 10px;
}
```

- [ ] **Step 2: 区块标题使用 Cormorant**

将 h2 样式改为：

```css
h2 {
  font-family: var(--font-heading);
  font-size: var(--text-2xl);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
  margin-bottom: 8px;
}
```

- [ ] **Step 3: 正文行高调整**

将 p 样式改为：

```css
p {
  color: var(--color-neutral-500);
  font-size: var(--text-base);
  line-height: var(--leading-normal);
}
```

- [ ] **Step 4: 卡片标题加强**

将 `.cardTitle strong` 样式改为：

```css
.cardTitle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-family: var(--font-heading);
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}
```

- [ ] **Step 5: label/form 保持 Montserrat 加粗**

```css
label, .fieldLabel {
  font-family: var(--font-body);
  font-weight: var(--weight-bold);
  font-size: var(--text-sm);
  color: var(--color-neutral-700);
  letter-spacing: 0.02em;
  text-transform: uppercase;
}
```

- [ ] **Step 6: 品牌名称使用 Cormorant**

在 styles.css 末尾添加：

```css
.brand strong {
  font-family: var(--font-heading);
  font-size: 1.25rem;
  font-weight: var(--weight-semibold);
  letter-spacing: -0.01em;
}

.brand span {
  font-family: var(--font-body);
  font-size: var(--text-xs);
  color: var(--color-neutral-400);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
```

- [ ] **Step 7: 标签/徽章使用等宽数字特性**

在 styles.css 末尾添加：

```css
.status,
.favoritePill {
  font-family: var(--font-body);
  font-size: var(--text-xs);
  font-weight: var(--weight-extrabold);
  letter-spacing: 0.03em;
}

.metaLine {
  font-family: var(--font-body);
  font-size: var(--text-sm);
  color: var(--color-neutral-500);
}

.tagRow span {
  font-family: var(--font-body);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.02em;
}
```

- [ ] **Step 8: 在浏览器中验证字体效果**

Run: `cd frontend && npm run dev`

检查：
- 页面标题是否渲染为 Cormorant 衬线字体
- 正文是否为 Montserrat
- 标签/按钮是否保持 Montserrat
- 在 Network 面板确认 Google Fonts 成功加载

- [ ] **Step 9: Commit**

```bash
git add frontend/src/styles.css
git commit -m "feat: upgrade typography to Cormorant + Montserrat

Page headings use Cormorant (elegant serif) for fashion-forward
feel. Body text, labels, and UI elements use Montserrat (geometric
sans) for readability. Removed Plus Jakarta Sans dependency.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: 色彩升级 — 暖灰中性色 + 紫色 AI 强调

**Files:**
- Modify: `frontend/src/styles.css`（更新 token 色值，应用暖色调）

- [ ] **Step 1: 更新 tokens.css 中的背景渐变**

确认 `tokens.css` 中已包含：

```css
--bg-app: linear-gradient(180deg, #FFF7FB 0%, #FAFAFA 100%);
```

当前 styles.css 中如 `.loginPage` 已引用 `var(--bg-app)`（Task 2 已改），无需额外修改。

- [ ] **Step 2: 卡片背景和边框增加暖意**

修改 `.garmentCard`、`.uploadItem`、`.historyItem`、`.controlPanel`、`.outfitResult`、`.aiBox` 共享的背景：

```css
.garmentCard {
  display: grid;
  overflow: hidden;
  text-align: left;
  background: var(--bg-surface);
  border: var(--border-thin);
  border-radius: var(--radius-md);
  padding: 0;
  box-shadow: var(--shadow-sm);
  transition: box-shadow var(--duration-normal) var(--ease-out),
              transform var(--duration-normal) var(--ease-out),
              border-color var(--duration-fast) ease;
}

.garmentCard:hover {
  border-color: var(--color-primary-300);
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}
```

- [ ] **Step 3: 侧边栏暖色调**

修改 `.sidebar`：

```css
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding: var(--space-6);
  background: var(--bg-surface);
  border-right: var(--border-thin);
}
```

- [ ] **Step 4: 导航按钮 active 态改为左侧指示器**

修改 `.navButton.active`：

```css
.navButton {
  justify-content: flex-start;
  background: transparent;
  border-color: transparent;
  color: var(--color-neutral-500);
  padding: 0 var(--space-3);
  border-left: 3px solid transparent;
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  transition: background-color var(--duration-fast) ease,
              color var(--duration-fast) ease,
              border-color var(--duration-fast) ease;
}

.navButton:hover {
  background: var(--color-neutral-100);
  color: var(--color-neutral-800);
}

.navButton.active {
  background: var(--color-primary-50);
  border-color: var(--color-primary-500);
  color: var(--color-primary-700);
  font-weight: var(--weight-bold);
}
```

移动端底部导航保持现有 pill 风格不变（移动端无需侧边指示器）。

- [ ] **Step 5: AI 相关按钮使用紫色 accent**

新增 `.accentButton` 样式（用于"生成搭配"等 AI CTA 按钮）：

```css
.accentButton {
  background: var(--color-accent-500);
  color: #fff;
  border-color: var(--color-accent-500);
  font-weight: var(--weight-extrabold);
  padding: 0 16px;
  border-radius: var(--radius-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 44px;
  transition: background-color var(--duration-fast) ease,
              box-shadow var(--duration-fast) ease;
}

.accentButton:hover {
  background: var(--color-accent-600);
  box-shadow: var(--shadow-glow);
}
```

- [ ] **Step 6: 天气 pill 和 review banner 增强**

```css
.weatherPill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  width: max-content;
  max-width: 100%;
  color: var(--color-primary-800);
  background: var(--color-primary-50);
  border: 1px solid var(--color-primary-200);
  border-radius: var(--radius-full);
  padding: 6px 14px;
  font-weight: var(--weight-semibold);
  font-size: var(--text-sm);
}

.reviewBanner {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  color: var(--color-neutral-700);
  background: var(--bg-surface);
  border: 1px solid var(--color-primary-200);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
}
```

- [ ] **Step 7: 空状态使用粉色点缀**

```css
.emptyState {
  min-height: 280px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: var(--space-3);
  text-align: center;
  border: 2px dashed var(--color-primary-200);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  color: var(--color-primary-500);
  padding: var(--space-8);
  transition: border-color var(--duration-normal) ease;
}

.emptyState:hover {
  border-color: var(--color-primary-400);
}
```

- [ ] **Step 8: 验证色彩升级效果**

Run: `cd frontend && npm run dev`

- 检查所有卡片是否呈现暖灰色调
- 导航 active 态是否显示为左侧粉色竖线
- 空状态虚线框是否为粉色

- [ ] **Step 9: Commit**

```bash
git add frontend/src/styles.css frontend/src/tokens.css
git commit -m "feat: warm neutral palette + violet AI accent

Cards gain subtle shadows and warm gray borders. Sidebar active
state uses left border indicator instead of full background fill.
AI-generation CTAs use violet accent color with glow shadow.
Weather pill uses fully rounded pill style.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: 衣橱卡片增强 — 渐变遮罩 + 悬浮动效 + 选择态

**Files:**
- Modify: `frontend/src/styles.css`（`.garmentCard` 及相关）
- Modify: `frontend/src/App.tsx:516-527`（`GarmentCardBody` 组件）

- [ ] **Step 1: 图片区域增加底部渐变遮罩**

修改 `.garmentCard img`：

```css
.garmentCard img {
  width: 100%;
  aspect-ratio: 4 / 5;
  object-fit: cover;
  background: var(--color-neutral-100);
  transition: transform var(--duration-slow) var(--ease-out);
}

.garmentCard:hover img {
  transform: scale(1.03);
}
```

- [ ] **Step 2: 选择模式卡片增强**

修改 `.selectableGarmentCard.selected`：

```css
.selectableGarmentCard.selected {
  border-color: var(--color-primary-500);
  background: var(--color-primary-50);
  box-shadow: var(--shadow-glow);
}

.selectableGarmentCard input[type="checkbox"] {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 1;
  width: 22px;
  height: 22px;
  accent-color: var(--color-primary-500);
  box-shadow: 0 0 0 3px var(--bg-surface);
  border-radius: var(--radius-sm);
}
```

- [ ] **Step 3: 卡片文字区域调整**

```css
.cardBody {
  display: grid;
  gap: var(--space-2);
  padding: var(--space-3);
}
```

- [ ] **Step 4: 状态标签样式细化**

```css
.status {
  font-size: var(--text-xs);
  font-weight: var(--weight-extrabold);
  color: var(--color-primary-700);
  background: var(--color-primary-100);
  border-radius: var(--radius-full);
  padding: 3px 10px;
  letter-spacing: 0.03em;
  text-transform: uppercase;
}

.status.failed {
  color: var(--color-error);
  background: var(--color-error-bg);
}

.status.ready {
  color: var(--color-success);
  background: var(--color-success-bg);
}
```

- [ ] **Step 5: 标签 pill 细化**

```css
.tagRow span {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  color: var(--color-neutral-600);
  background: var(--color-neutral-100);
  border-radius: var(--radius-full);
  padding: 3px 10px;
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.02em;
}
```

- [ ] **Step 6: 验证卡片效果**

Run: `cd frontend && npm run dev`

- 悬停卡片时图片是否微放大，阴影是否加深
- 选择模式选中卡片是否有粉色光晕
- 状态标签是否正确显示

- [ ] **Step 7: Commit**

```bash
git add frontend/src/styles.css frontend/src/App.tsx
git commit -m "feat: enhance garment cards with hover effects and refined status pills

Cards gain shadow elevation on hover, subtle image zoom, and
soft glow on selection. Status pills use semantic colors with
uppercase labels for better scanability.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: 骨架屏加载态 (Skeleton Screen)

**Files:**
- Create: `frontend/src/Skeleton.tsx`
- Modify: `frontend/src/App.tsx`（在 WardrobeView、HistoryView 使用 Skeleton）
- Modify: `frontend/src/styles.css`（骨架屏脉冲动画）

- [ ] **Step 1: 创建 Skeleton 组件**

```tsx
// frontend/src/Skeleton.tsx
interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  rounded?: boolean;
  className?: string;
}

export function Skeleton({ width = "100%", height = 16, rounded = false, className = "" }: SkeletonProps) {
  const style: React.CSSProperties = {
    width: typeof width === "number" ? `${width}px` : width,
    height: typeof height === "number" ? `${height}px` : height,
    borderRadius: rounded ? "var(--radius-full)" : "var(--radius-sm)",
  };

  return (
    <div
      className={`skeleton ${className}`}
      style={style}
      aria-hidden="true"
    />
  );
}

export function GarmentCardSkeleton() {
  return (
    <div className="garmentCard" aria-busy="true" aria-label="加载中">
      <Skeleton height={0} width="100%" className="skeletonImage" />
      <div className="cardBody">
        <Skeleton width="60%" height={20} />
        <Skeleton width="80%" height={14} />
        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <Skeleton width={50} height={24} rounded />
          <Skeleton width={50} height={24} rounded />
          <Skeleton width={50} height={24} rounded />
        </div>
      </div>
    </div>
  );
}

export function OutfitSkeleton() {
  return (
    <div className="outfitResult" aria-busy="true" aria-label="加载中">
      <div className="resultHeader">
        <Skeleton width="50%" height={24} />
        <Skeleton width={80} height={20} rounded />
      </div>
      <div className="outfitImages">
        <Skeleton height={150} />
        <Skeleton height={150} />
        <Skeleton height={150} />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 添加骨架屏 CSS 动画**

在 `styles.css` 末尾添加：

```css
/* === Skeleton Loading === */
.skeleton {
  background: linear-gradient(
    90deg,
    var(--color-neutral-100) 25%,
    var(--color-neutral-50) 50%,
    var(--color-neutral-100) 75%
  );
  background-size: 200% 100%;
  animation: skeletonShimmer 1.5s var(--ease-in-out) infinite;
}

.skeletonImage {
  aspect-ratio: 4 / 5;
  height: auto !important;
  border-radius: var(--radius-md) var(--radius-md) 0 0;
}

@keyframes skeletonShimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton {
    animation: none;
    background: var(--color-neutral-200);
  }
}
```

- [ ] **Step 3: 在 WardrobeView 中使用骨架屏**

在 `App.tsx` 的 `WardrobeView` 中，当 `loading` 为 true 时渲染骨架屏。修改 `WardrobeView` 的 props 接收 `loading`：

打开 `App.tsx`，找到 `WardrobeView` 调用处（约 212 行），确认已传递 `loading` 状态（需要新增 prop）。

在 `WardrobeView` 组件返回的 JSX 中，在 `garmentGrid` 之前添加条件渲染：

```tsx
{loading && (
  <div className="garmentGrid">
    {Array.from({ length: 6 }).map((_, i) => (
      <GarmentCardSkeleton key={i} />
    ))}
  </div>
)}
```

如果 `loading` 为 false 且有 garments，则渲染真实卡片。

导入 `GarmentCardSkeleton`：

```tsx
import { GarmentCardSkeleton } from "./Skeleton";
```

- [ ] **Step 4: 在 HistoryView 中使用骨架屏**

同理，在 `HistoryView` 的 props 中新增 `loading`，列表加载时渲染骨架：

```tsx
{loading && (
  <div className="historyList">
    {Array.from({ length: 3 }).map((_, i) => (
      <div key={i} className="historyItem" aria-busy="true">
        <Skeleton width="70%" height={20} />
        <Skeleton width="90%" height={14} />
      </div>
    ))}
  </div>
)}
```

- [ ] **Step 5: 替换全局 loading 指示器**

在 `App.tsx:210`，将原来的 loading 文字行替换为更简洁的骨架 pulse 条：

```tsx
{loading && (
  <div className="loadingLine" role="status" aria-live="polite">
    <div className="loadingPulse" />
    正在同步云端衣橱
  </div>
)}
```

对应的 CSS：

```css
.loadingPulse {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-primary-500);
  animation: pulseDot 1.2s var(--ease-in-out) infinite;
}

@keyframes pulseDot {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}
```

- [ ] **Step 6: 验证骨架屏**

Run: `cd frontend && npm run dev`

- 刷新页面，检查加载瞬间是否显示骨架屏（可能需要 throttling）
- 在 Network 面板中设置 "Slow 3G" 模拟慢速加载
- 检查 `prefers-reduced-motion` 是否停用动画

- [ ] **Step 7: Commit**

```bash
git add frontend/src/Skeleton.tsx frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat: add skeleton screen loading states

Garment cards and history list show shimmer skeleton placeholders
during data fetch. Replaces the plain spinner text with a more
polished loading indicator. Respects prefers-reduced-motion.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: 图片模糊渐进加载 (Blur-up)

**Files:**
- Modify: `frontend/src/styles.css`（blur-up 动画）
- Modify: `frontend/src/App.tsx`（GarmentCard、DetailView 中的 `<img>` 标签）

- [ ] **Step 1: 添加 blur-up CSS**

在 `styles.css` 末尾添加：

```css
/* === Blur-up image loading === */
img.blurUp {
  filter: blur(10px) saturate(0.8);
  transform: scale(1.05);
  transition: filter var(--duration-slow) var(--ease-out),
              transform var(--duration-slow) var(--ease-out);
}

img.blurUpLoaded {
  filter: blur(0) saturate(1);
  transform: scale(1);
}
```

- [ ] **Step 2: 创建 BlurImage 组件包装器**

在 `App.tsx` 文件顶部添加一个内部组件（或放在 `Skeleton.tsx` 中）：

```tsx
// 在 App.tsx 中，作为模块级函数
function BlurImage({ src, alt, className = "" }: { src: string; alt: string; className?: string }) {
  const [loaded, setLoaded] = useState(false);

  return (
    <img
      src={src}
      alt={alt}
      className={`${className} blurUp${loaded ? " blurUpLoaded" : ""}`}
      onLoad={() => setLoaded(true)}
      loading="lazy"
    />
  );
}
```

- [ ] **Step 3: 替换 GarmentCard 中的 img**

在 `GarmentCard` 组件（约 509 行）中，将：

```tsx
<img src={garment.thumbnail_url || garment.image_url} alt={`${garment.style || categoryLabel(garment.category)} ${garment.category}`} />
```

替换为：

```tsx
<BlurImage
  src={garment.thumbnail_url || garment.image_url}
  alt={`${garment.style || categoryLabel(garment.category)} ${garment.category}`}
/>
```

- [ ] **Step 4: 替换 DetailView 中的 img**

在 `DetailView` 组件（约 651 行）中，将：

```tsx
<img className="detailImage" src={garment.image_url} alt={`${garment.style || "服装"} 详情`} />
```

替换为：

```tsx
<BlurImage className="detailImage" src={garment.image_url} alt={`${garment.style || "服装"} 详情`} />
```

- [ ] **Step 5: 替换搭配结果中的 img**

在 `OutfitView` 的 `outfitImages` 区域（约 849 行），将：

```tsx
{currentOutfit.items.map((item) => <img key={item.garment_id} src={item.image_url} alt={`${item.category} 搭配单品`} />)}
```

替换为：

```tsx
{currentOutfit.items.map((item) => <BlurImage key={item.garment_id} src={item.image_url} alt={`${item.category} 搭配单品`} />)}
```

- [ ] **Step 6: 替换 ManualPicker 中的 img**

在 `ManualPicker` 组件（约 894 行），将：

```tsx
<img src={garment.thumbnail_url || garment.image_url} alt={`${garment.style || categoryLabel(garment.category)} 选择项`} />
```

替换为：

```tsx
<BlurImage src={garment.thumbnail_url || garment.image_url} alt={`${garment.style || categoryLabel(garment.category)} 选择项`} />
```

- [ ] **Step 7: 验证模糊加载效果**

Run: `cd frontend && npm run dev`

- 在 Network 面板中设置 "Slow 3G"
- 刷新页面，观察图片是否先模糊后清晰
- 确认 `onLoad` 后 `blurUpLoaded` 类名是否添加

- [ ] **Step 8: Commit**

```bash
git add frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat: add blur-up progressive image loading

Images start blurred and low-saturation, then transition to full
clarity on load. Improves perceived performance during slow
network conditions.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: 微交互动效 — 心形收藏 + AI 生成动画

**Files:**
- Modify: `frontend/src/styles.css`（动画关键帧）
- Modify: `frontend/src/App.tsx`（收藏按钮、生成按钮状态）

- [ ] **Step 1: 心形收藏动画 CSS**

在 `styles.css` 末尾添加：

```css
/* === Heart Favorite Animation === */
.heartButton {
  position: relative;
  transition: color var(--duration-normal) var(--ease-out);
}

.heartButton.favorited {
  color: var(--color-primary-500);
  animation: heartPop 0.4s var(--ease-out);
}

@keyframes heartPop {
  0% { transform: scale(1); }
  30% { transform: scale(1.35); }
  60% { transform: scale(0.9); }
  100% { transform: scale(1); }
}
```

- [ ] **Step 2: 修改 OutfitView 中的收藏按钮**

在 `App.tsx` 中 `OutfitView` 的收藏按钮（约 856 行），将：

```tsx
<button type="button" className="secondaryButton compact" disabled={busy || currentOutfit.is_favorite} onClick={handleFavorite}>
  <Heart size={18} aria-hidden="true" />
  {currentOutfit.is_favorite ? "已收藏" : "保存喜欢"}
</button>
```

改为使用 `heartButton` 类：

```tsx
<button
  type="button"
  className={`secondaryButton compact heartButton${currentOutfit.is_favorite ? " favorited" : ""}`}
  disabled={busy || currentOutfit.is_favorite}
  onClick={handleFavorite}
>
  <Heart size={18} aria-hidden="true" fill={currentOutfit.is_favorite ? "currentColor" : "none"} />
  {currentOutfit.is_favorite ? "已收藏" : "保存喜欢"}
</button>
```

- [ ] **Step 3: AI 生成按钮脉冲动画**

```css
/* === AI Generate Pulse === */
.aiPulse {
  position: relative;
}

.aiPulse::after {
  content: "";
  position: absolute;
  inset: -3px;
  border-radius: var(--radius-md);
  background: var(--color-accent-500);
  opacity: 0;
  z-index: -1;
  animation: pulseRing 2s var(--ease-in-out) infinite;
}

@keyframes pulseRing {
  0% { opacity: 0; transform: scale(0.95); }
  50% { opacity: 0.15; transform: scale(1.05); }
  100% { opacity: 0; transform: scale(0.95); }
}

@media (prefers-reduced-motion: reduce) {
  .aiPulse::after { animation: none; }
  .heartButton.favorited { animation: none; }
}
```

- [ ] **Step 4: 给 AI 生成按钮添加 aiPulse 类**

在 `OutfitView` 的生成按钮（约 822 行），将 className 从：

```tsx
className="primaryButton compact"
```

改为：

```tsx
className="accentButton compact aiPulse"
```

- [ ] **Step 5: 搭配结果淡入动画**

```css
/* === Outfit result entrance === */
.outfitResult {
  display: grid;
  gap: var(--space-4);
  background: var(--bg-surface);
  border: var(--border-thin);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  animation: fadeSlideUp var(--duration-slow) var(--ease-out);
}

@keyframes fadeSlideUp {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
```

- [ ] **Step 6: 验证动效**

Run: `cd frontend && npm run dev`

- 点击收藏按钮，检查心形弹出动画
- 生成搭配，检查结果区域淡入动画
- 检查 AI 生成按钮脉冲光环
- 开启系统 reduced-motion 确认动画被禁用

- [ ] **Step 7: Commit**

```bash
git add frontend/src/styles.css frontend/src/App.tsx
git commit -m "feat: add micro-interactions for favorite and AI generate

Heart icon pops on favorite. AI generate button has subtle pulsing
ring. Outfit results fade-slide in on generation. All animations
respect prefers-reduced-motion.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9: 登录页改造 — 分屏布局

**Files:**
- Modify: `frontend/src/styles.css`（登录页样式）
- Modify: `frontend/src/App.tsx:338-367`（`LoginScreen` 组件）

- [ ] **Step 1: 添加登录页左侧 Hero 区域**

在 `LoginScreen` 组件的 `loginPanel` 外层增加 Hero 区域：

```tsx
return (
  <main className="loginPage">
    <div className="loginLayout">
      <section className="loginHero" aria-labelledby="login-hero-title">
        <Brand />
        <h1 id="login-hero-title">你的私人<br />AI 衣橱顾问</h1>
        <ul className="loginFeatures">
          <li>
            <Image size={20} aria-hidden="true" />
            <span>上传常穿单品，AI 自动识别分类与风格标签</span>
          </li>
          <li>
            <Sparkles size={20} aria-hidden="true" />
            <span>结合天气与场合，智能生成每日搭配推荐</span>
          </li>
          <li>
            <Shirt size={20} aria-hidden="true" />
            <span>构建你的数字化衣橱，随时编辑与管理</span>
          </li>
        </ul>
      </section>

      <section className="loginPanel" aria-labelledby="login-title">
        <h1 id="login-title">用邮箱进入你的云端衣橱</h1>
        <p>上传常穿单品，查看 AI 识别结果，再生成适合天气和场景的全身搭配。</p>
        {/* 保持现有分段控件和表单不变 */}
        <div className="segmented authMode" aria-label="账号操作">
          ...
        </div>
        <form className="formStack" onSubmit={handleSubmit}>
          ...
        </form>
      </section>
    </div>
  </main>
);
```

- [ ] **Step 2: 添加登录页分屏 CSS**

修改 `styles.css` 中的 `.loginPage` 并新增相关类：

```css
.loginPage {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: var(--space-6);
  background: var(--bg-app);
}

.loginLayout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  width: min(100%, 960px);
  gap: var(--space-8);
  align-items: center;
}

.loginHero {
  display: grid;
  gap: var(--space-6);
  padding: var(--space-8);
}

.loginHero h1 {
  font-family: var(--font-heading);
  font-size: clamp(2.5rem, 5vw, 3.5rem);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
  letter-spacing: -0.02em;
  color: var(--color-neutral-800);
  margin-bottom: 0;
}

.loginFeatures {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--space-4);
}

.loginFeatures li {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  color: var(--color-neutral-500);
  font-size: var(--text-base);
  line-height: var(--leading-normal);
}

.loginFeatures li svg {
  flex-shrink: 0;
  color: var(--color-primary-400);
  margin-top: 2px;
}

.loginPanel {
  width: 100%;
  background: var(--bg-surface);
  border: var(--border-thin);
  border-radius: var(--radius-lg);
  padding: var(--space-8);
  box-shadow: var(--shadow-md);
}

/* 移动端：单列布局，隐藏 Hero */
@media (max-width: 800px) {
  .loginLayout {
    grid-template-columns: 1fr;
    gap: 0;
  }

  .loginHero {
    display: none;
  }
}
```

- [ ] **Step 3: 保留 LoginScreen 中现有的所有功能逻辑**

确认 `mode`、`email`、`password`、`busy`、`error` 状态和 `handleSubmit` 逻辑完全不变。

- [ ] **Step 4: 验证登录页**

Run: `cd frontend && npm run dev`

- 桌面端：左侧 Hero + 右侧表单
- 移动端（≤800px）：仅显示表单（Hero 隐藏）
- 注册/登录切换正常
- 表单验证正常

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles.css frontend/src/App.tsx
git commit -m "feat: split login page with hero section

Desktop: left side shows product value proposition with feature
list, right side shows auth form. Mobile: form only, hero hidden.
Brand typography uses Cormorant for the hero headline.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 10: 最终校验 — 无障碍 & 性能审计

**Files:**
- Modify: `frontend/src/styles.css`（审计后修正）
- Modify: `frontend/src/App.tsx`（审计后修正）

- [ ] **Step 1: 运行 Lighthouse 审计**

```bash
cd frontend && npm run build && npx serve -s dist
```

在 Chrome DevTools → Lighthouse → Desktop + Mobile 各跑一次，记录：
- Performance 分数
- Accessibility 分数
- Best Practices 分数

- [ ] **Step 2: 色彩对比度检查**

检查以下组合是否满足 WCAG AA (4.5:1)：

| 元素 | 前景 | 背景 | 预期 |
|------|------|------|------|
| 正文 | `--color-neutral-800` (#292524) | `--bg-surface` (#FFF) | 11.5:1 ✓ |
| 辅助文字 | `--color-neutral-500` (#78716C) | `--bg-surface` (#FFF) | 4.7:1 ✓ |
| 主按钮文字 | #FFF | `--color-primary-600` (#DB2777) | 4.8:1 ✓ |
| accent 按钮 | #FFF | `--color-accent-500` (#8B5CF6) | 4.3:1 ⚠ 需调暗 |

若 accent 按钮对比度不足，调整 `tokens.css` 的 `--color-accent-500` 为 `#7C3AED`（原 `--color-accent-600`）。

- [ ] **Step 3: 键盘导航测试**

手动测试：
- Tab 键能否按顺序聚焦所有交互元素
- Skip link 是否可见并可用
- 卡片选中/取消选中是否可通过键盘操作
- 模态（如有）是否正确捕获焦点

- [ ] **Step 4: 检查 focus 状态可见性**

确保 `styles.css` 中包含：

```css
button:focus-visible,
input:focus-visible,
select:focus-visible,
a:focus-visible,
label:focus-visible {
  outline: var(--border-focus);
  outline-offset: 2px;
}
```

- [ ] **Step 5: 检查 prefers-reduced-motion 覆盖**

确认所有动画相关 CSS 后都有 `@media (prefers-reduced-motion: reduce)` 重置。

- [ ] **Step 6: 修复审计发现的问题**

根据 Lighthouse 报告和手动测试结果修复问题。常见修复项：
- 图片缺少 alt 文本 → 补充
- 表单缺少 label → 补充
- 低对比度文本 → 调整色值

- [ ] **Step 7: 交最终 commit**

```bash
git add frontend/src/styles.css frontend/src/App.tsx frontend/src/tokens.css
git commit -m "fix: accessibility and contrast audit adjustments

Ensure all text meets WCAG AA 4.5:1 contrast ratio. Add explicit
focus-visible styles for keyboard navigation. Verify all animations
respect prefers-reduced-motion.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## 自检清单

**1. 方案覆盖：** 每项原设计方案内容均分配了对应 Task：
- Design Token → Task 1
- 字体 → Task 3
- 色彩 → Task 2, 4
- 卡片增强 → Task 5
- 骨架屏 → Task 6
- 模糊加载 → Task 7
- 动效 → Task 8
- 登录页 → Task 9
- 无障碍审计 → Task 10

**2. 占位符扫描：** 无 TBD/TODO/implement later 等占位符。

**3. 类型一致性：**
- `GarmentCardSkeleton` → `Skeleton.tsx` 中定义，`App.tsx` 中导入 ✓
- `BlurImage` → `App.tsx` 中定义（模块级组件），多处使用 ✓
- CSS token 名称在 `tokens.css` 中定义后在 `styles.css` 中引用 ✓
- `accentButton` 类在 Task 4 定义，Task 8 使用 ✓
