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
