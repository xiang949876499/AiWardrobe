import {
  Archive,
  Check,
  ExternalLink,
  Heart,
  Home,
  Image,
  Loader2,
  LogOut,
  MapPin,
  Plus,
  Search,
  Shirt,
  ShoppingBag,
  Sparkles,
  Store,
  Trash2,
  UploadCloud,
  Wand2
} from "lucide-react";
import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

import {
  analyzeShoppingRecommendationItem,
  analyzePurchaseImage,
  analyzePurchaseUrl,
  clearToken,
  createShoppingRecommendations,
  createManualOutfit,
  deleteGarment,
  deleteOutfit,
  fetchGarments,
  fetchOutfits,
  fetchTodayWeather,
  generateOutfit,
  getStoredToken,
  loginWithPassword,
  registerWithPassword,
  savePurchaseCandidate,
  setOutfitFavorite,
  setOutfitFixed,
  storeToken,
  updateGarment,
  uploadGarmentPhoto,
  uploadPlainGarment
} from "./api";
import type {
  Category,
  Garment,
  Occasion,
  Outfit,
  PurchaseCandidate,
  ShoppingRecommendationItem,
  ShoppingRecommendationRun,
  ShoppingRecommendationTarget,
  UploadSession,
  Weather
} from "./types";
import { GarmentCardSkeleton } from "./Skeleton";

const categories: Array<{ value: "all" | Category; label: string }> = [
  { value: "all", label: "全部" },
  { value: "top", label: "上衣" },
  { value: "bottom", label: "下装" },
  { value: "outerwear", label: "外套" },
  { value: "shoes", label: "鞋子" },
  { value: "bag", label: "包" },
  { value: "accessory", label: "饰品" }
];

const occasions: Array<{ value: Occasion; label: string }> = [
  { value: "work", label: "上班" },
  { value: "date", label: "约会" },
  { value: "sport", label: "运动" },
  { value: "formal", label: "正式场合" },
  { value: "casual", label: "休闲" }
];

const navItems = [
  { id: "wardrobe", label: "衣橱", icon: Home },
  { id: "upload", label: "上传", icon: UploadCloud },
  { id: "purchase", label: "购买分析", icon: ShoppingBag },
  { id: "shopping", label: "推荐购买", icon: Store },
  { id: "outfit", label: "搭配", icon: Sparkles },
  { id: "history", label: "历史", icon: Archive },
  { id: "tryon", label: "AI 换装", icon: Wand2 }
] as const;

type View = (typeof navItems)[number]["id"] | "detail";

type UploadItem = {
  name: string;
  status: "上传中" | "入库中" | "单品拆分中" | "标签识别中" | "已入库" | "失败";
  session?: UploadSession;
  garments?: Garment[];
  message?: string;
};

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

function App() {
  const [token, setToken] = useState<string | null>(() => getStoredToken());
  const [activeView, setActiveView] = useState<View>("wardrobe");
  const [garments, setGarments] = useState<Garment[]>([]);
  const [selectedGarment, setSelectedGarment] = useState<Garment | null>(null);
  const [outfits, setOutfits] = useState<Outfit[]>([]);
  const [currentOutfit, setCurrentOutfit] = useState<Outfit | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (token) void refreshGarments(token);
  }, [token]);

  async function refreshGarments(authToken = token) {
    if (!authToken) return;
    setLoading(true);
    setError("");
    try {
      const response = await fetchGarments(authToken);
      setGarments(response.items);
    } catch (err) {
      if (isAuthError(err)) {
        handleLogout();
        return;
      }
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function loadHistory(favorite?: boolean) {
    if (!token) return;
    setLoading(true);
    setError("");
    try {
      const response = await fetchOutfits(token, favorite);
      setOutfits(response.items);
    } catch (err) {
      if (isAuthError(err)) {
        handleLogout();
        return;
      }
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleDeleteOutfit(id: string) {
    if (!token) return;
    setLoading(true);
    setError("");
    try {
      await deleteOutfit(token, id);
      setOutfits((items) => items.filter((item) => item.id !== id));
      setCurrentOutfit((outfit) => (outfit?.id === id ? null : outfit));
    } catch (err) {
      if (isAuthError(err)) {
        handleLogout();
        return;
      }
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleBatchDeleteGarments(ids: string[]) {
    if (!token || ids.length === 0) return false;
    setLoading(true);
    setError("");
    try {
      await Promise.all(ids.map((id) => deleteGarment(token, id)));
      setGarments((items) => items.filter((item) => !ids.includes(item.id)));
      if (selectedGarment && ids.includes(selectedGarment.id)) {
        setSelectedGarment(null);
      }
      return true;
    } catch (err) {
      if (isAuthError(err)) {
        handleLogout();
        return false;
      }
      setError(errorMessage(err));
      return false;
    } finally {
      setLoading(false);
    }
  }

  function openView(view: View) {
    setActiveView(view);
    if (view === "history") void loadHistory();
  }

  function handleLogout() {
    clearToken();
    setToken(null);
    setGarments([]);
    setOutfits([]);
    setCurrentOutfit(null);
    setActiveView("wardrobe");
  }

  if (!token) {
    return (
      <LoginScreen
        onLogin={(nextToken) => {
          storeToken(nextToken);
          setToken(nextToken);
        }}
      />
    );
  }

  return (
    <div className="appShell">
      <aside className="sidebar" aria-label="主导航">
        <Brand />
        <nav className="sideNav">
          {navItems.map((item) => <NavButton key={item.id} item={item} activeView={activeView} onOpen={openView} />)}
        </nav>
        <button type="button" className="ghostButton logoutButton" onClick={handleLogout}>
          <LogOut size={18} aria-hidden="true" />
          退出登录
        </button>
      </aside>

      <main className="main" id="main">
        <a className="skipLink" href="#main">跳到主要内容</a>
        {error && <div className="alert" role="alert">{error}</div>}
        {loading && <div className="loadingLine"><Loader2 size={16} aria-hidden="true" /> 正在同步云端衣橱</div>}
        {activeView === "wardrobe" && (
          <WardrobeView
            garments={garments}
            loading={loading}
            onSelect={(garment) => {
              setSelectedGarment(garment);
              setActiveView("detail");
            }}
            onUpload={() => setActiveView("upload")}
            onBatchDelete={(ids) => handleBatchDeleteGarments(ids)}
          />
        )}
        {activeView === "upload" && (
          <UploadView
            token={token}
            onAuthExpired={handleLogout}
            onPending={(nextGarments) => {
              setGarments((items) => mergeGarments(nextGarments, items));
            }}
            onConfirm={(garment) => {
              setSelectedGarment(garment);
              setActiveView("detail");
            }}
          />
        )}
        {activeView === "purchase" && (
          <PurchaseAnalysisView
            token={token}
            onAuthExpired={handleLogout}
            onSaved={(garment) => {
              setGarments((items) => mergeGarments([garment], items));
              setActiveView("wardrobe");
            }}
          />
        )}
        {activeView === "shopping" && (
          <ShoppingRecommendationsView
            token={token}
            onAuthExpired={handleLogout}
            onSaved={(garment) => {
              setGarments((items) => mergeGarments([garment], items));
              setActiveView("wardrobe");
            }}
          />
        )}
        {activeView === "detail" && selectedGarment && (
          <DetailView
            token={token}
            garment={selectedGarment}
            onSaved={(garment) => {
              setSelectedGarment(garment);
              setGarments((items) => mergeGarments([garment], items));
              setActiveView("wardrobe");
            }}
            onDeleted={(id) => {
              setSelectedGarment(null);
              setGarments((items) => items.filter((item) => item.id !== id));
              setActiveView("wardrobe");
            }}
          />
        )}
        {activeView === "outfit" && (
          <OutfitView
            token={token}
            garments={garments}
            currentOutfit={currentOutfit}
            setCurrentOutfit={setCurrentOutfit}
          />
        )}
        {activeView === "history" && (
          <HistoryView
            outfits={outfits}
            onFavorites={() => void loadHistory(true)}
            onAll={() => void loadHistory()}
            onOpen={(outfit) => {
              setCurrentOutfit(outfit);
              setActiveView("outfit");
            }}
            onDelete={(id) => void handleDeleteOutfit(id)}
          />
        )}
        {activeView === "tryon" && <TryOnView />}
      </main>

      <nav className="bottomNav" aria-label="移动端主导航">
        {navItems.map((item) => <NavButton key={item.id} item={item} activeView={activeView} onOpen={openView} mobile />)}
      </nav>
    </div>
  );
}

function Brand() {
  return (
    <div className="brand">
      <div className="brandMark"><Shirt size={22} aria-hidden="true" /></div>
      <div>
        <strong>AiWardrobe</strong>
        <span>AI 个人衣橱</span>
      </div>
    </div>
  );
}

function NavButton({ item, activeView, onOpen, mobile = false }: {
  item: (typeof navItems)[number];
  activeView: View;
  onOpen: (view: View) => void;
  mobile?: boolean;
}) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      aria-label={mobile ? `移动端${item.label}` : undefined}
      className={activeView === item.id ? (mobile ? "bottomNavButton active" : "navButton active") : (mobile ? "bottomNavButton" : "navButton")}
      onClick={() => onOpen(item.id)}
    >
      <Icon size={18} aria-hidden="true" />
      <span>{item.label}</span>
    </button>
  );
}

function LoginScreen({ onLogin }: { onLogin: (token: string) => void }) {
  const [mode, setMode] = useState<"register" | "login">("register");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  const passwordValid = password.length >= 8;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = mode === "register"
        ? await registerWithPassword(email, password)
        : await loginWithPassword(email, password);
      onLogin(response.access_token);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

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
          <div className="segmented authMode" aria-label="账号操作">
            <button type="button" className={mode === "register" ? "segment active" : "segment"} onClick={() => setMode("register")}>注册</button>
            <button type="button" className={mode === "login" ? "segment active" : "segment"} onClick={() => setMode("login")}>登录</button>
          </div>
          <form className="formStack" onSubmit={handleSubmit}>
            <label htmlFor="email">邮箱</label>
            <input id="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
            <label htmlFor="password">密码</label>
            <input
              id="password"
              type="password"
              autoComplete={mode === "register" ? "new-password" : "current-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <div className="hint">密码至少 8 位。注册后会直接进入衣橱。</div>
            {error && <div className="alert" role="alert">{error}</div>}
            <button type="submit" className="primaryButton" disabled={!emailValid || !passwordValid || busy}>
              {mode === "register" ? "注册账号" : "登录账号"}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}

function WardrobeView({ garments, onSelect, onUpload, onBatchDelete, loading }: {
  garments: Garment[];
  onSelect: (garment: Garment) => void;
  onUpload: () => void;
  onBatchDelete: (ids: string[]) => Promise<boolean>;
  loading: boolean;
}) {
  const [category, setCategory] = useState<"all" | Category>("all");
  const [search, setSearch] = useState("");
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const pending = garments.filter((garment) => garment.status === "pending_review");
  const ready = garments.filter((garment) => garment.status === "ready");
  const filtered = useMemo(() => garments.filter((garment) => {
    const categoryMatches = category === "all" || garment.category === category;
    const text = [garment.category, garment.style, garment.material, ...garment.tags, ...garment.colors].join(" ").toLowerCase();
    return categoryMatches && text.includes(search.toLowerCase());
  }), [category, garments, search]);
  const selectedCount = selectedIds.length;

  function toggleSelection(id: string) {
    setSelectedIds((ids) => ids.includes(id) ? ids.filter((item) => item !== id) : [...ids, id]);
  }

  function exitSelectionMode() {
    setSelectionMode(false);
    setSelectedIds([]);
  }

  async function handleBatchDelete() {
    if (selectedIds.length === 0) return;
    if (!window.confirm(`确定删除选中的 ${selectedIds.length} 件衣服吗？删除后将不会进入搭配推荐。`)) {
      return;
    }
    const deleted = await onBatchDelete(selectedIds);
    if (deleted) exitSelectionMode();
  }

  return (
    <section className="pageSection" aria-labelledby="wardrobe-title">
      <div className="pageHeader">
        <div>
          <h1 id="wardrobe-title">我的衣橱</h1>
          <p>上传后的单品会直接进入衣橱；你可以随时编辑标签来优化搜索和搭配。</p>
        </div>
        <div className="wardrobeActions">
          {garments.length > 0 && (
            <button type="button" className="secondaryButton compact" onClick={() => selectionMode ? exitSelectionMode() : setSelectionMode(true)}>
              {selectionMode ? "取消管理" : "批量管理"}
            </button>
          )}
          <button type="button" className="primaryButton compact" onClick={onUpload}>
            <Plus size={18} aria-hidden="true" />
            上传衣服
          </button>
        </div>
      </div>
      {pending.length > 0 && (
        <div className="reviewBanner">
          <strong>待编辑单品</strong>
          <span>{pending.length} 件旧单品需要编辑标签后会自动转为已入库。</span>
        </div>
      )}
      <div className="toolbar">
        <label className="searchField">
          <Search size={16} aria-hidden="true" />
          <span className="srOnly">搜索衣服</span>
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索标签、颜色、材质" />
        </label>
      </div>
      <div className="segmented" aria-label="服装分类">
        {categories.map((item) => (
          <button key={item.value} type="button" className={category === item.value ? "segment active" : "segment"} onClick={() => setCategory(item.value)}>
            {item.label}
          </button>
        ))}
      </div>
      {selectionMode && (
        <div className="batchBar" role="status" aria-live="polite">
          <span>已选 {selectedCount} 件</span>
          <div className="buttonRow">
            <button type="button" className="ghostButton compact" onClick={exitSelectionMode}>取消</button>
            <button type="button" className="dangerButton compact" disabled={selectedCount === 0} onClick={handleBatchDelete}>
              <Trash2 size={18} aria-hidden="true" />
              删除所选
            </button>
          </div>
        </div>
      )}
      {loading && (
        <div className="garmentGrid">
          {Array.from({ length: 6 }).map((_, i) => (
            <GarmentCardSkeleton key={i} />
          ))}
        </div>
      )}
      {!loading && (filtered.length === 0 ? (
        <div className="emptyState">
          <Shirt size={34} aria-hidden="true" />
          <h2>还没有衣服</h2>
          <p>上传几件常穿单品，AI 才能开始帮你搭配。</p>
          <button type="button" className="primaryButton compact" onClick={onUpload}>开始上传</button>
        </div>
      ) : (
        <>
          {ready.length === 0 && <div className="hint">当前还没有已入库单品，请先上传衣服。</div>}
          <div className="garmentGrid">
            {filtered.map((garment) => (
              <GarmentCard
                key={garment.id}
                garment={garment}
                onSelect={onSelect}
                selectionMode={selectionMode}
                selected={selectedIds.includes(garment.id)}
                onToggleSelection={toggleSelection}
              />
            ))}
          </div>
        </>
      ))}
    </section>
  );
}

function GarmentCard({ garment, onSelect, selectionMode = false, selected = false, onToggleSelection }: {
  garment: Garment;
  onSelect: (garment: Garment) => void;
  selectionMode?: boolean;
  selected?: boolean;
  onToggleSelection?: (id: string) => void;
}) {
  if (selectionMode) {
    return (
      <label className={selected ? "garmentCard selectableGarmentCard selected" : "garmentCard selectableGarmentCard"}>
        <input
          type="checkbox"
          checked={selected}
          aria-label={`选择 ${garment.category} ${garment.id}`}
          onChange={() => onToggleSelection?.(garment.id)}
        />
        <BlurImage src={garment.thumbnail_url || garment.image_url} alt={`${garment.style || categoryLabel(garment.category)} ${garment.category}`} />
        <GarmentCardBody garment={garment} />
      </label>
    );
  }

  return (
    <button type="button" className="garmentCard" onClick={() => onSelect(garment)} aria-label={`编辑${categoryLabel(garment.category)} ${garment.style || ""}`}>
      <BlurImage src={garment.thumbnail_url || garment.image_url} alt={`${garment.style || categoryLabel(garment.category)} ${garment.category}`} />
      <GarmentCardBody garment={garment} />
    </button>
  );
}

function GarmentCardBody({ garment }: { garment: Garment }) {
  return (
    <div className="cardBody">
      <div className="cardTitle">
        <strong>{categoryLabel(garment.category)}</strong>
        <span className={`status ${garment.status}`}>{statusLabel(garment.status)}</span>
      </div>
      <div className="metaLine">{garment.colors.join(" / ") || "未设置颜色"} · {garment.material || "未设置材质"}</div>
      <div className="tagRow">{garment.tags.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div>
    </div>
  );
}

function UploadView({ token, onAuthExpired, onPending, onConfirm }: {
  token: string;
  onAuthExpired: () => void;
  onPending: (garments: Garment[]) => void;
  onConfirm: (garment: Garment) => void;
}) {
  const [mode, setMode] = useState<"plain" | "auto">("plain");
  const [items, setItems] = useState<UploadItem[]>([]);

  async function handleFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files || []);
    setItems(files.map((file) => ({ name: file.name, status: "上传中" })));
    for (const file of files) {
      setItems((current) => current.map((item) => item.name === file.name ? { ...item, status: mode === "plain" ? "入库中" : "单品拆分中" } : item));
      try {
        if (mode === "plain") {
          const garment = await uploadPlainGarment(token, file);
          setItems((current) => current.map((item) => item.name === file.name ? { ...item, status: "已入库", garments: [garment] } : item));
          onPending([garment]);
        } else {
          const session = await uploadGarmentPhoto(token, file);
          setItems((current) => current.map((item) => item.name === file.name ? { ...item, status: "已入库", session, garments: session.garments } : item));
          onPending(session.garments);
        }
      } catch (err) {
        if (isAuthError(err)) {
          onAuthExpired();
          return;
        }
        setItems((current) => current.map((item) => item.name === file.name ? { ...item, status: "失败", message: errorMessage(err) } : item));
      }
    }
  }

  return (
    <section className="pageSection" aria-labelledby="upload-title">
      <div className="pageHeader">
        <div>
          <h1 id="upload-title">上传衣服</h1>
          <p>{mode === "plain" ? "普通上传不调用拆分工作流，请上传单独一件衣服、包或饰品的图片；系统会直接用 VL 模型打标签。" : "自动识别适合整套或多单品照片，会调用 RunningHub 工作流并预留识别扣费接口。"}</p>
        </div>
      </div>
      <div className="segmented" aria-label="上传模式">
        <button type="button" className={mode === "plain" ? "segment active" : "segment"} onClick={() => setMode("plain")}>普通上传</button>
        <button type="button" className={mode === "auto" ? "segment active" : "segment"} onClick={() => setMode("auto")}>自动识别</button>
      </div>
      <label className="uploadBox" htmlFor="garment-upload">
        <UploadCloud size={34} aria-hidden="true" />
        <strong>{mode === "plain" ? "选择单件图片" : "选择整套或多单品照片"}</strong>
        <span>{mode === "plain" ? "每张图片只放一件衣服、包或饰品；上传后打标签并直接入库" : "支持多人或多单品照片；单个文件失败不影响其他图片"}</span>
        <input id="garment-upload" aria-label={mode === "plain" ? "选择单件图片" : "选择整套或多单品照片"} type="file" accept="image/*" multiple onChange={handleFiles} />
      </label>
      <div className="uploadList">
        {items.map((item) => (
          <div key={item.name} className="uploadItem">
            <span>{item.name}</span>
            <strong>{item.status === "已入库" ? (mode === "plain" ? "已入库，可编辑标签" : "单品拆分完成") : item.status}</strong>
            {item.message && <small>{item.message}</small>}
            {(item.session || item.garments) && (
              <div className="uploadPreview">
                <div>
                  <strong>已入库单品</strong>
                  <div className="miniGrid">
                    {(item.garments || item.session?.garments || []).map((garment) => <GarmentCard key={garment.id} garment={garment} onSelect={onConfirm} />)}
                  </div>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function PurchaseAnalysisView({ token, onAuthExpired, onSaved }: {
  token: string;
  onAuthExpired: () => void;
  onSaved: (garment: Garment) => void;
}) {
  const [url, setUrl] = useState("");
  const [candidate, setCandidate] = useState<PurchaseCandidate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [manualFallback, setManualFallback] = useState(false);
  const [saveMessage, setSaveMessage] = useState("");
  const canAnalyze = /^https?:\/\/\S+\.\S+/.test(url.trim());

  async function handleAnalyze(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSaveMessage("");
    setManualFallback(false);
    try {
      const result = await analyzePurchaseUrl(token, url.trim());
      setCandidate(result);
    } catch (err) {
      if (isAuthError(err)) {
        onAuthExpired();
        return;
      }
      const message = errorMessage(err);
      if (["product_image_not_found", "page_fetch_failed", "image_download_failed", "invalid_url"].includes(message)) {
        setManualFallback(true);
        setError("未能自动找到商品图片，请上传商品图继续分析。");
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleManualImage(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const result = await analyzePurchaseImage(token, file, url.trim());
      setCandidate(result);
      setManualFallback(false);
    } catch (err) {
      if (isAuthError(err)) {
        onAuthExpired();
        return;
      }
      setError(errorMessage(err));
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function handleSave() {
    if (!candidate) return;
    setBusy(true);
    setError("");
    try {
      const garment = await savePurchaseCandidate(token, candidate.id);
      setSaveMessage("已加入衣橱");
      onSaved(garment);
    } catch (err) {
      if (isAuthError(err)) {
        onAuthExpired();
        return;
      }
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="pageSection" aria-labelledby="purchase-title">
      <div className="pageHeader">
        <div>
          <h1 id="purchase-title">购买分析</h1>
          <p>输入商品链接，系统会提取商品图、识别单品标签，并和当前衣橱对比后给出购买建议。</p>
        </div>
      </div>
      <form className="controlPanel" onSubmit={handleAnalyze}>
        <label htmlFor="purchase-url">商品链接</label>
        <input
          id="purchase-url"
          type="url"
          value={url}
          placeholder="https://example.com/product/123"
          onChange={(event) => setUrl(event.target.value)}
        />
        <button type="submit" className="primaryButton compact" disabled={!canAnalyze || busy}>
          {busy ? <Loader2 size={18} aria-hidden="true" /> : <ShoppingBag size={18} aria-hidden="true" />}
          {busy ? "分析中" : "开始分析"}
        </button>
        {error && <div className="alert" role="alert">{error}</div>}
        {manualFallback && (
          <label className="uploadBox purchaseUploadBox" htmlFor="purchase-image-upload">
            <UploadCloud size={28} aria-hidden="true" />
            <strong>上传商品图片</strong>
            <span>适合商品页无法自动提取图片时继续分析。</span>
            <input id="purchase-image-upload" aria-label="上传商品图片" type="file" accept="image/*" onChange={handleManualImage} />
          </label>
        )}
      </form>
      {candidate && (
        <article className={`outfitResult purchaseResult recommendation-${candidate.recommendation}`}>
          <div className="purchaseResultGrid">
            <BlurImage className="purchaseImage" src={candidate.image_url} alt={`${candidate.title || "商品"} 图片`} />
            <div className="purchaseSummary">
              <div className="resultHeader">
                <strong>{candidate.title || candidate.domain || "商品候选"}</strong>
                <span className={`status recommendationBadge ${candidate.recommendation}`}>
                  {recommendationLabel(candidate.recommendation)} · {candidate.score}
                </span>
              </div>
              <p>{candidate.reason_summary}</p>
              <div className="tagRow">
                <span>{categoryLabel(candidate.category)}</span>
                {candidate.colors.map((color) => <span key={color}>{color}</span>)}
                {candidate.tags.slice(0, 4).map((tag) => <span key={tag}>{tag}</span>)}
              </div>
              <div className="analysisStats">
                <span>重复度 {Math.round(Number(candidate.analysis.duplicate_score || 0))}</span>
                <span>缺口 {Math.round(Number(candidate.analysis.wardrobe_gap_score || 0))}</span>
                <span>搭配 {Math.round(Number(candidate.analysis.pairing_score || 0))}</span>
              </div>
              <div className="buttonRow">
                <button type="button" className="primaryButton compact" disabled={busy || candidate.status === "saved"} onClick={handleSave}>
                  <Check size={18} aria-hidden="true" />
                  加入衣橱
                </button>
                {saveMessage && <span className="favoritePill">{saveMessage}</span>}
              </div>
            </div>
          </div>
          {candidate.similar_items.length > 0 && (
            <div className="similarStrip" aria-label="相似衣橱单品">
              {candidate.similar_items.map((item) => (
                <div key={item.garment_id} className="similarItem">
                  <BlurImage src={item.image_url} alt="相似衣橱单品" />
                  <strong>{Math.round(item.similarity)}%</strong>
                  <span>{item.matched_reasons.slice(0, 2).join(" / ")}</span>
                </div>
              ))}
            </div>
          )}
        </article>
      )}
    </section>
  );
}

const shoppingTargets: Array<{ value: ShoppingRecommendationTarget; label: string }> = [
  { value: "auto_gap", label: "自动补齐" },
  { value: "work", label: "通勤" },
  { value: "date", label: "约会" },
  { value: "sport", label: "运动" },
  { value: "summer", label: "夏季" },
  { value: "basics", label: "基础款" }
];

function ShoppingRecommendationsView({ token, onAuthExpired, onSaved }: {
  token: string;
  onAuthExpired: () => void;
  onSaved: (garment: Garment) => void;
}) {
  const [target, setTarget] = useState<ShoppingRecommendationTarget>("auto_gap");
  const [run, setRun] = useState<ShoppingRecommendationRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [savedIds, setSavedIds] = useState<string[]>([]);

  async function handleFetch(refresh = false) {
    setBusy(true);
    setError("");
    try {
      const result = await createShoppingRecommendations(token, { target, refresh });
      setRun(result);
    } catch (err) {
      if (isAuthError(err)) {
        onAuthExpired();
        return;
      }
      setError(formatShoppingError(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleAnalyze(item: ShoppingRecommendationItem) {
    setBusy(true);
    setError("");
    try {
      const analyzed = await analyzeShoppingRecommendationItem(token, item.id);
      setRun((current) => current ? {
        ...current,
        items: current.items.map((existing) => existing.id === analyzed.id ? analyzed : existing)
      } : current);
    } catch (err) {
      if (isAuthError(err)) {
        onAuthExpired();
        return;
      }
      setError(formatShoppingError(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSave(item: ShoppingRecommendationItem) {
    if (!item.purchase_candidate_id) return;
    setBusy(true);
    setError("");
    try {
      const garment = await savePurchaseCandidate(token, item.purchase_candidate_id);
      setSavedIds((ids) => [...ids, item.id]);
      onSaved(garment);
    } catch (err) {
      if (isAuthError(err)) {
        onAuthExpired();
        return;
      }
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="pageSection" aria-labelledby="shopping-title">
      <div className="pageHeader">
        <div>
          <h1 id="shopping-title">推荐购买</h1>
          <p>按衣橱缺口或场景目标获取淘宝 / 天猫候选单品，先分析再决定是否加入衣橱。</p>
        </div>
      </div>

      <div className="controlPanel">
        <div className="fieldLabel">推荐目标</div>
        <div className="segmented" aria-label="推荐目标">
          {shoppingTargets.map((item) => (
            <button
              key={item.value}
              type="button"
              className={target === item.value ? "segment active" : "segment"}
              onClick={() => setTarget(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="buttonRow">
          <button type="button" className="primaryButton compact" disabled={busy} onClick={() => handleFetch(false)}>
            {busy ? <Loader2 size={18} aria-hidden="true" /> : <Sparkles size={18} aria-hidden="true" />}
            获取推荐
          </button>
          <button type="button" className="secondaryButton compact" disabled={busy} onClick={() => handleFetch(true)}>
            <Search size={18} aria-hidden="true" />
            重新搜索
          </button>
        </div>
        {run?.keywords.length ? (
          <div className="tagRow" aria-label="推荐关键词">
            {run.keywords.map((keyword) => <span key={keyword}>{keyword}</span>)}
            {run.cache_hit && <span>已复用缓存</span>}
          </div>
        ) : null}
      </div>

      {error && <div className="alert" role="alert">{error}</div>}

      {!run && (
        <div className="emptyState">
          <Store size={34} aria-hidden="true" />
          <h2>选择目标后获取推荐</h2>
          <p>系统会从当前衣橱出发，优先补齐缺少的品类和适合场景的单品。</p>
        </div>
      )}

      {run && run.items.length === 0 && (
        <div className="emptyState">
          <Search size={34} aria-hidden="true" />
          <h2>没有找到合适商品</h2>
          <p>换一个目标或稍后重新搜索。</p>
        </div>
      )}

      {run && run.items.length > 0 && (
        <div className="shoppingStream">
          {run.items.map((item) => (
            <article key={item.id} className="shoppingProductCard">
              <a href={item.product_url} target="_blank" rel="noreferrer" aria-label={`查看${item.title}`}>
                <BlurImage className="shoppingProductImage" src={item.image_url} alt={item.title} />
              </a>
              <div className="shoppingProductBody">
                <div className="resultHeader">
                  <div>
                    <h2>{item.title}</h2>
                    <p>{item.shop_name} · ¥{item.price}</p>
                  </div>
                  <span className={`recommendationBadge ${item.recommendation || item.analysis_status}`}>
                    {shoppingItemStatusLabel(item)}
                  </span>
                </div>
                {item.score !== null && <strong className="shoppingScore">{item.score} 分</strong>}
                {item.reason_summary && <p>{item.reason_summary}</p>}
                {item.similar_items.length > 0 && (
                  <div className="similarStrip" aria-label="相似衣橱单品">
                    {item.similar_items.map((similar) => (
                      <div key={similar.garment_id} className="similarItem">
                        <BlurImage src={similar.image_url} alt="相似衣橱单品" />
                        <strong>{Math.round(similar.similarity)}%</strong>
                        <span>{similar.matched_reasons.slice(0, 2).join(" / ")}</span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="buttonRow">
                  {item.analysis_status === "pending_analysis" || item.analysis_status === "failed" ? (
                    <button type="button" className="secondaryButton compact" disabled={busy} onClick={() => handleAnalyze(item)}>
                      <Sparkles size={18} aria-hidden="true" />
                      分析此商品
                    </button>
                  ) : null}
                  {item.purchase_candidate_id && (
                    <button
                      type="button"
                      className="primaryButton compact"
                      disabled={busy || savedIds.includes(item.id)}
                      onClick={() => handleSave(item)}
                    >
                      <Check size={18} aria-hidden="true" />
                      加入衣橱
                    </button>
                  )}
                  <a className="secondaryButton compact" href={item.product_url} target="_blank" rel="noreferrer">
                    <ExternalLink size={18} aria-hidden="true" />
                    查看淘宝
                  </a>
                  {savedIds.includes(item.id) && <span className="favoritePill">已加入衣橱</span>}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function DetailView({ token, garment, onSaved, onDeleted }: {
  token: string;
  garment: Garment;
  onSaved: (garment: Garment) => void;
  onDeleted: (id: string) => void;
}) {
  const [form, setForm] = useState({
    tags: garment.tags.join(", ")
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const saved = await updateGarment(token, garment.id, {
        tags: splitValues(form.tags)
      } as Partial<Garment>);
      onSaved(saved);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!window.confirm("确定删除这件衣服吗？删除后将不会进入搭配推荐。")) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await deleteGarment(token, garment.id);
      onDeleted(garment.id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="pageSection detailLayout" aria-labelledby="detail-title">
      <div>
        <BlurImage className="detailImage" src={garment.image_url} alt={`${garment.style || "服装"} 详情`} />
        <div className="aiBox">
          <strong>识别信息</strong>
          <span>类别 {categoryLabel(garment.category)}</span>
          <span>颜色 {garment.colors.join(" / ") || "未知"}</span>
          <span>材质 {garment.material || "未知"}</span>
          <span>风格 {garment.style || "未知"}</span>
          <span>季节 {garment.season.join(" / ") || "未知"}</span>
          <span>版型 {garment.fit || "未知"}</span>
          <span>置信度 {Math.round(garment.ai_confidence * 100)}%</span>
          {garment.crop_box && <span>裁剪框 {garment.crop_box.width} × {garment.crop_box.height}</span>}
          <code>{JSON.stringify(garment.ai_result)}</code>
        </div>
      </div>
      <form className="detailForm" onSubmit={handleSubmit}>
        <h1 id="detail-title">编辑单品标签</h1>
        <p>单品已经入库。这里只保留标签修改，用来影响搜索、筛选和后续搭配偏好。</p>
        <label htmlFor="tags">标签</label>
        <input id="tags" value={form.tags} onChange={(event) => setForm({ ...form, tags: event.target.value })} />
        {error && <div className="alert" role="alert">{error}</div>}
        <button type="submit" className="primaryButton stickyAction" disabled={busy}>
          <Check size={18} aria-hidden="true" />
          保存标签
        </button>
        <button type="button" className="dangerButton" disabled={busy} onClick={handleDelete}>
          <Trash2 size={18} aria-hidden="true" />
          删除衣服
        </button>
      </form>
    </section>
  );
}

function OutfitView({ token, garments, currentOutfit, setCurrentOutfit }: {
  token: string;
  garments: Garment[];
  currentOutfit: Outfit | null;
  setCurrentOutfit: (outfit: Outfit) => void;
}) {
  const [mode, setMode] = useState<"ai" | "manual">("ai");
  const [occasion, setOccasion] = useState<Occasion>("work");
  const [temperature, setTemperature] = useState(22);
  const [weather, setWeather] = useState<Weather | null>(null);
  const [weatherStatus, setWeatherStatus] = useState<"loading" | "ready" | "failed">("loading");
  const [weatherMessage, setWeatherMessage] = useState("正在获取天气");
  const [manualName, setManualName] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const readyGarments = garments.filter((garment) => garment.status === "ready");

  async function loadWeather(lat: number, lon: number, placeLabel?: string) {
    setWeatherStatus("loading");
    try {
      const nextWeather = await fetchTodayWeather(token, lat, lon);
      setWeather(nextWeather);
      setWeatherMessage(`${placeLabel || nextWeather.city} · ${nextWeather.condition} · ${nextWeather.temperature}°C`);
      setTemperature(nextWeather.temperature);
      setWeatherStatus("ready");
    } catch {
      setWeatherStatus("failed");
      setWeatherMessage("天气服务暂不可用，可继续生成");
    }
  }

  useEffect(() => {
    const loadDefaultCityWeather = () => {
      void loadWeather(31.2304, 121.4737, "默认城市");
    };

    if (!navigator.geolocation) {
      loadDefaultCityWeather();
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => {
        void loadWeather(position.coords.latitude, position.coords.longitude);
      },
      loadDefaultCityWeather
    );
  }, [token]);

  async function handleGenerate() {
    setBusy(true);
    setError("");
    try {
      const outfit = await generateOutfit(token, { occasion, temperature: weather?.temperature ?? temperature, weather });
      setCurrentOutfit(outfit);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleFavorite() {
    if (!currentOutfit) return;
    setBusy(true);
    try {
      const updated = await setOutfitFavorite(token, currentOutfit.id, true);
      setCurrentOutfit(updated);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleFixed() {
    if (!currentOutfit) return;
    setBusy(true);
    try {
      const updated = await setOutfitFixed(token, currentOutfit.id, !currentOutfit.is_fixed);
      setCurrentOutfit(updated);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleManualSave() {
    if (selectedIds.length === 0) {
      setError("请至少选择一件单品");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const outfit = await createManualOutfit(token, {
        name: manualName,
        garment_ids: selectedIds,
        occasion,
        temperature: weather?.temperature ?? temperature,
        is_fixed: true,
        weather
      });
      setCurrentOutfit(outfit);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="pageSection" aria-labelledby="outfit-title">
      <div className="pageHeader">
        <div>
          <h1 id="outfit-title">搭配</h1>
          <p>使用已入库单品，结合场合与今日天气生成 AI 推荐，也可以自己选择并保存固定搭配。</p>
        </div>
        <div className="weatherPill"><MapPin size={16} aria-hidden="true" />{weatherMessage}</div>
      </div>
      <div className="segmented" aria-label="搭配模式">
        <button type="button" className={mode === "ai" ? "segment active" : "segment"} onClick={() => setMode("ai")}>AI 推荐</button>
        <button type="button" className={mode === "manual" ? "segment active" : "segment"} onClick={() => setMode("manual")}>自己搭配</button>
      </div>
      <div className="controlPanel">
        <div className="segmented" aria-label="场合">
          {occasions.map((item) => (
            <button key={item.value} type="button" className={occasion === item.value ? "segment active" : "segment"} onClick={() => setOccasion(item.value)}>
              {item.label}
            </button>
          ))}
        </div>
        <div className="fieldLabel">当前季节</div>
        <div className="readonlyField" aria-label="当前季节">按节气自动判断</div>
        <label htmlFor="temperature">温度：{temperature}°C</label>
        <input id="temperature" type="range" min="-10" max="40" value={temperature} onChange={(event) => setTemperature(Number(event.target.value))} />
        {mode === "ai" ? (
          <button type="button" className="accentButton compact aiPulse" disabled={busy || weatherStatus === "loading"} onClick={handleGenerate}>
            <Sparkles size={18} aria-hidden="true" />
            {weatherStatus === "loading" ? "获取天气中" : busy ? "生成中" : "生成搭配"}
          </button>
        ) : (
          <ManualPicker
            garments={readyGarments}
            selectedIds={selectedIds}
            setSelectedIds={setSelectedIds}
            name={manualName}
            setName={setManualName}
            onSave={handleManualSave}
            busy={busy || weatherStatus === "loading"}
          />
        )}
        {error && <div className="alert" role="alert">{error}</div>}
      </div>
      {currentOutfit && (
        <article className="outfitResult">
          <div className="resultHeader">
            <strong>{currentOutfit.name || (currentOutfit.source === "manual" ? "自定义搭配" : "AI 推荐搭配")}</strong>
            <div className="tagRow">
              <span>{currentOutfit.source === "manual" ? "手动搭配" : "AI 搭配"}</span>
              {currentOutfit.is_fixed && <span>固定搭配</span>}
            </div>
          </div>
          <div className="outfitImages">
            {currentOutfit.items.map((item) => <BlurImage key={item.garment_id} src={item.image_url} alt={`${item.category} 搭配单品`} />)}
          </div>
          <p>{currentOutfit.explanation}</p>
          <div className="tagRow">
            {currentOutfit.items.map((item) => <span key={item.garment_id}>{item.reason}</span>)}
          </div>
          <div className="buttonRow">
            <button
              type="button"
              className={`secondaryButton compact heartButton${currentOutfit.is_favorite ? " favorited" : ""}`}
              disabled={busy || currentOutfit.is_favorite}
              onClick={handleFavorite}
            >
              <Heart size={18} aria-hidden="true" fill={currentOutfit.is_favorite ? "currentColor" : "none"} />
              {currentOutfit.is_favorite ? "已收藏" : "保存喜欢"}
            </button>
            <button type="button" className="secondaryButton compact" disabled={busy} onClick={handleFixed}>
              <Check size={18} aria-hidden="true" />
              {currentOutfit.is_fixed ? "取消固定" : "保存固定搭配"}
            </button>
          </div>
        </article>
      )}
    </section>
  );
}

function ManualPicker({ garments, selectedIds, setSelectedIds, name, setName, onSave, busy }: {
  garments: Garment[];
  selectedIds: string[];
  setSelectedIds: (ids: string[]) => void;
  name: string;
  setName: (name: string) => void;
  onSave: () => void;
  busy: boolean;
}) {
  function toggle(id: string) {
    setSelectedIds(selectedIds.includes(id) ? selectedIds.filter((item) => item !== id) : [...selectedIds, id]);
  }

  return (
    <div className="manualPanel">
      <label htmlFor="manual-name">固定搭配名称</label>
      <input id="manual-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：周一通勤" />
      <div className="manualGrid">
        {garments.length === 0 ? (
          <div className="hint">暂无已入库单品。</div>
        ) : garments.map((garment) => (
          <label key={garment.id} className="manualChoice">
            <input type="checkbox" checked={selectedIds.includes(garment.id)} onChange={() => toggle(garment.id)} />
            <BlurImage src={garment.thumbnail_url || garment.image_url} alt={`${garment.style || categoryLabel(garment.category)} 选择项`} />
            <span>{categoryLabel(garment.category)} · {garment.style || "未设置风格"}</span>
          </label>
        ))}
      </div>
      <button type="button" className="primaryButton compact" disabled={busy || selectedIds.length === 0} onClick={onSave}>
        保存固定搭配
      </button>
    </div>
  );
}

function HistoryView({ outfits, onFavorites, onAll, onOpen, onDelete }: {
  outfits: Outfit[];
  onFavorites: () => void;
  onAll: () => void;
  onOpen: (outfit: Outfit) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <section className="pageSection" aria-labelledby="history-title">
      <div className="pageHeader">
        <div>
          <h1 id="history-title">搭配历史</h1>
          <p>区分 AI 搭配、手动搭配和固定搭配，方便回看。</p>
        </div>
      </div>
      <div className="buttonRow">
        <button type="button" className="secondaryButton compact" onClick={onAll}>全部历史</button>
        <button type="button" className="secondaryButton compact" onClick={onFavorites}>只看收藏</button>
      </div>
      {outfits.length === 0 ? (
        <div className="emptyState">
          <Archive size={34} aria-hidden="true" />
          <h2>还没有搭配历史</h2>
          <p>生成或保存一套搭配后，这里会自动记录。</p>
        </div>
      ) : (
        <div className="historyList">
          {outfits.map((outfit) => (
            <div key={outfit.id} className="historyItem">
              <button type="button" className="historyOpenButton" onClick={() => onOpen(outfit)}>
                <strong>{outfit.name || occasionLabel(outfit.occasion)} · {outfit.temperature ?? "--"}°C</strong>
                <span>{outfit.explanation}</span>
                <div className="tagRow">
                  <span>{outfit.source === "manual" ? "手动搭配" : "AI 搭配"}</span>
                  {outfit.is_fixed && <span>固定搭配</span>}
                  {outfit.is_favorite && <span>已收藏</span>}
                </div>
              </button>
              <button type="button" className="iconDangerButton" aria-label="删除搭配" onClick={() => onDelete(outfit.id)}>
                <Trash2 size={17} aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function TryOnView() {
  return (
    <section className="pageSection" aria-labelledby="tryon-title">
      <div className="pageHeader">
        <div>
          <h1 id="tryon-title">AI 换装</h1>
          <p>这里预留形象生成能力入口，当前版本只展示未开放状态。</p>
        </div>
      </div>
      <div className="tryonGrid">
        {[
          { title: "换装", description: "上传人体照并替换服装的能力入口。", icon: Wand2 },
          { title: "换发型", description: "预留发型风格变换入口。", icon: Sparkles },
          { title: "换背景", description: "预留背景替换入口。", icon: Image }
        ].map((item) => {
          const Icon = item.icon;
          return (
            <article key={item.title} className="tryonCard" aria-disabled="true">
              <Icon size={24} aria-hidden="true" />
              <strong>{item.title}</strong>
              <p>{item.description}</p>
              <span className="status">未开放</span>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function categoryLabel(category: string) {
  return categories.find((item) => item.value === category)?.label || category;
}

function occasionLabel(occasion: string) {
  return occasions.find((item) => item.value === occasion)?.label || occasion;
}

function recommendationLabel(recommendation: string) {
  return {
    recommend: "推荐",
    consider: "考虑",
    skip: "跳过"
  }[recommendation] || recommendation;
}

function shoppingItemStatusLabel(item: ShoppingRecommendationItem) {
  if (item.recommendation) return recommendationLabel(item.recommendation);
  return {
    pending_analysis: "待分析",
    analyzing: "分析中",
    analyzed: "已分析",
    failed: "分析失败"
  }[item.analysis_status] || item.analysis_status;
}

function statusLabel(status: string) {
  return {
    uploaded: "已上传",
    extracting: "拆分中",
    tagging: "打标签中",
    pending_review: "待编辑",
    processing: "识别中",
    ready: "已入库",
    failed: "失败"
  }[status] || status;
}

function splitValues(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function mergeGarments(incoming: Garment[], existing: Garment[]) {
  const byId = new Map(existing.map((garment) => [garment.id, garment]));
  incoming.forEach((garment) => byId.set(garment.id, garment));
  return Array.from(byId.values()).sort((a, b) => b.created_at.localeCompare(a.created_at));
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

function formatShoppingError(error: unknown) {
  const message = errorMessage(error);
  if (message.includes("recommendation_rate_limited")) return "推荐刷新太频繁，请稍后再试";
  if (message.includes("taobao_rate_limited")) return "电商平台请求过于频繁，请稍后再试";
  if (message.includes("analysis_rate_limited")) return "商品分析太频繁，请稍后再试";
  return message;
}

function isAuthError(error: unknown) {
  return error instanceof Error && /invalid bearer token|not authenticated|could not validate/i.test(error.message);
}

export default App;
