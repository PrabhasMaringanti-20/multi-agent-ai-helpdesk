import { BrowserRouter } from "react-router-dom";

import { AppRoutes } from "@/router/routes";
import { ToastContainer } from "@/shared/ui/ToastContainer";

export function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
      <ToastContainer />
    </BrowserRouter>
  );
}
