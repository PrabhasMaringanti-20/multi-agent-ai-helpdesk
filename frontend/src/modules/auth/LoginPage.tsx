import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@/modules/auth/useAuth";
import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Input } from "@/shared/ui/Input";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [orgSlug, setOrgSlug] = useState("acme");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate(
      { org_slug: orgSlug, email, password },
      { onSuccess: () => navigate("/", { replace: true }) },
    );
  };
  
  const fillCredentials = (demoEmail: string) => {
    setOrgSlug("acme");
    setEmail(demoEmail);
    setPassword("ChangeMe123!");
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-100 px-4 py-8">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <h1 className="text-2xl font-bold text-slate-900">Enterprise AI Helpdesk</h1>
          <p className="text-sm text-slate-500">Sign in to your workspace</p>
        </div>
        <Card>
          <form className="space-y-4" onSubmit={onSubmit}>
            <Input label="Organization" name="org_slug" value={orgSlug}
              onChange={(e) => setOrgSlug(e.target.value)} required />
            <Input label="Email" name="email" type="email" value={email}
              onChange={(e) => setEmail(e.target.value)} required />
            <Input label="Password" name="password" type="password" value={password}
              onChange={(e) => setPassword(e.target.value)} required />
            <Button type="submit" className="w-full" loading={login.isPending}>
              Sign in
            </Button>
          </form>
          <p className="mt-4 text-center text-sm text-slate-500">
            No account?{" "}
            <button className="font-medium text-brand-600" onClick={() => navigate("/register")}>
              Register
            </button>
          </p>
        </Card>
        
        <div className="mt-6 rounded-md bg-blue-50 p-4 border border-blue-100">
          <h3 className="text-sm font-semibold text-blue-800 mb-2">Portfolio Demo Credentials:</h3>
          <div className="space-y-2 text-xs text-blue-900">
            <button onClick={() => fillCredentials('admin@acme.com')} className="w-full text-left px-3 py-2 rounded bg-white hover:bg-blue-100 border border-blue-200 transition-colors">
              <span className="font-bold">Admin:</span> admin@acme.com
            </button>
            <button onClick={() => fillCredentials('support@acme.com')} className="w-full text-left px-3 py-2 rounded bg-white hover:bg-blue-100 border border-blue-200 transition-colors">
              <span className="font-bold">Support Engineer:</span> support@acme.com
            </button>
            <button onClick={() => fillCredentials('sme@acme.com')} className="w-full text-left px-3 py-2 rounded bg-white hover:bg-blue-100 border border-blue-200 transition-colors">
              <span className="font-bold">Subject Matter Expert:</span> sme@acme.com
            </button>
            <p className="pt-2 text-center text-blue-700 italic">Password for all accounts: ChangeMe123!</p>
          </div>
        </div>
      </div>
    </div>
  );
}
