import { Suspense } from "react";
import { AuthForm } from "@/features/AuthForm";

export default function LoginPage() {
  return <Suspense><AuthForm mode="login" /></Suspense>;
}
