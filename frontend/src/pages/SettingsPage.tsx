import { useNavigate } from 'react-router-dom'
import { ProfileForm } from '@/components/shared/ProfileForm'

export function SettingsPage() {
  const navigate = useNavigate()

  return (
    <div className="mx-auto max-w-md">
      <h1 className="mb-6 font-display text-lg font-bold tracking-tight text-text-primary">Settings</h1>
      <ProfileForm onSaved={() => navigate('/')} />
      <p className="mt-4 text-center text-xs text-text-tertiary">
        Tokens are stored locally in your browser and sent only when validating, previewing, or creating issues.
      </p>
    </div>
  )
}
