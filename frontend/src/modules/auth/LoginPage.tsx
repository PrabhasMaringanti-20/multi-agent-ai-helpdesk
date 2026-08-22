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

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-4">
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
      </div>
    </div>
  );
}
