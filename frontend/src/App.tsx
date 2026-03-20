import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'

import { Layout } from '@/components/layout/Layout'

// Lazy-loaded pages (will be created in Phase 2)
// For now, create placeholder pages
import { RegisterPage } from '@/pages/RegisterPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { StatusPage } from '@/pages/StatusPage'
import { HistoryPage } from '@/pages/HistoryPage'
import { ReportPage } from '@/pages/ReportPage'
import { TestHistoryPage } from '@/pages/TestHistoryPage'

export default function App() {
  return (
    <BrowserRouter basename="/">
      <Routes>
        <Route path="/register" element={<RegisterPage />} />
        <Route element={<Layout />}>
          <Route index element={<DashboardPage />} />
          <Route path="/dashboard" element={<Navigate to="/" replace />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/history/test/:testName" element={<TestHistoryPage />} />
          <Route path="/results/:jobId" element={<ReportPage />} />
          <Route path="/status/:jobId" element={<StatusPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
