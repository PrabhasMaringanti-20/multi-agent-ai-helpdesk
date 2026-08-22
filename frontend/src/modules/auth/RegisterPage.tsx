import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@/modules/auth/useAuth";
import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Input } from "@/shared/ui/Input";

export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ org_slug: "acme", email: "", password: "", full_name: "" });

  const update = (key: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    register.mutate(form, { onSuccess: () => navigate("/login") });
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-4">
      <div className="w-full max-w-md">
        <h1 className="mb-6 text-center text-2xl font-bold text-slate-900">Create your account</h1>
        <Card>
          <form className="space-y-4" onSubmit={onSubmit}>
            <Input label="Organization" name="org_slug" value={form.org_slug} onChange={update("org_slug")} required />
            <Input label="Full name" name="full_name" value={form.full_name} onChange={update("full_name")} />
            <Input label="Email" name="email" type="email" value={form.email} onChange={update("email")} required />
            <Input label="Password" name="password" type="password" value={form.password} onChange={update("password")} required minLength={8} />
            <Button type="submit" className="w-full" loading={register.isPending}>
              Create account
            </Button>
          </form>
          <p className="mt-4 text-center text-sm text-slate-500">
            Already have an account?{" "}
            <button className="font-medium text-brand-600" onClick={() => navigate("/login")}>
              Sign in
            </button>
          </p>
        </Card>
      </div>
    </div>
  );
}
