# Soft Luxury 历史重设计：最终校验与自检

本文保留原历史计划的 Task 10 与自检要求，不代表本轮已执行。设计目标及 Task 1～9 见[主计划](2026-05-28-frontend-soft-luxury-redesign.md)。

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
