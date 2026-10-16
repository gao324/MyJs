import { cn } from '@/lib/cn'

interface Props {
  title: string
  subtitle?: React.ReactNode
  /** 标题右侧、subtitle 之前的额外节点(如状态徽标) */
  titleExtra?: React.ReactNode
  right?: React.ReactNode
  className?: string
}

export function PageHeader({ title, subtitle, titleExtra, right, className }: Props) {
  return (
    <header
      className={cn(
        'px-5 pt-3 pb-2 border-b border-border flex items-center justify-between gap-4',
        /* 手机端右侧工具栏 (筛选/日期/按钮组) 会折成多行, 与标题抢宽度时标题被压到只剩
           几个字。改成标题独占一行、工具栏另起一行, 两边都不再互相挤压。 */
        'max-md:flex-col max-md:items-stretch max-md:gap-2',
        className,
      )}
    >
      <div className="flex min-w-0 flex-col justify-center gap-0.5">
        <div className="flex items-center gap-2">
          <h1 className="shrink-0 whitespace-nowrap bg-gradient-to-b from-foreground via-foreground to-accent/80 bg-clip-text text-xl font-semibold tracking-normal text-transparent">
            {title}
          </h1>
          {titleExtra}
        </div>
        {subtitle && (
          <span className="truncate whitespace-nowrap text-[10px] leading-tight text-muted">{subtitle}</span>
        )}
      </div>
      {right}
    </header>
  )
}
