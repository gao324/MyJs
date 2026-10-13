import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { QueryClient, QueryCache } from '@tanstack/react-query'
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client'
import { initializeFrontendExtensions } from './extensions/bootstrap'
import { installTspExtBridge, loadRuntimePageExtensions } from './extensions/runtime'
import { api } from './lib/api'
import { QK } from './lib/queryKeys'
import { cn } from './lib/cn'
import * as format from './lib/format'
import { createAppPersister, shouldPersistQuery, PERSIST_BUSTER } from './lib/queryPersist'
// 字体自托管 (@fontsource): 替代 rsms.me / Google Fonts 渲染阻塞外链,
// 内网/离线部署不再白屏等字体。权重覆盖 tailwind 全部用量 (300-900)。
import '@fontsource/inter/300.css'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/inter/900.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/600.css'
import '@fontsource/jetbrains-mono/700.css'
import './index.css'

// 开箱即用模式: 后端登录校验已关闭, 不再拦截 401 跳登录页 (保留函数体便于恢复)。
const _redirectToLogin = (() => {
  let redirecting = false
  return (_err: unknown) => {
    void redirecting
    return
  }
})()

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (err) => _redirectToLogin(err),
  }),
  defaultOptions: {
    queries: {
      staleTime: 5_000,           // 5s 内复用,与 §4.2 Repository 不变量一致
      refetchOnWindowFocus: false,
    },
    mutations: {
      onError: (err) => _redirectToLogin(err),
    },
  },
})

async function bootstrap() {
  await initializeFrontendExtensions()
  // 页面包能力桥: 运行时页面包经 shim 读取宿主 api/QK/cn/format
  installTspExtBridge({ api, QK, cn, format })
  // 运行时页面包: 必须在 router 模块求值(finalize + 建路由表)之前挂载
  await loadRuntimePageExtensions()
  const { router } = await import('./router')
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <PersistQueryClientProvider
        client={queryClient}
        persistOptions={{
          persister: createAppPersister(),
          buster: PERSIST_BUSTER,
          // 恢复超过 1 天的缓存直接丢弃 (慢变族一天内必然后台刷新过)
          maxAge: 24 * 60 * 60 * 1000,
          dehydrateOptions: { shouldDehydrateQuery: shouldPersistQuery },
        }}
      >
        <RouterProvider router={router} />
      </PersistQueryClientProvider>
    </React.StrictMode>,
  )
}

void bootstrap()
