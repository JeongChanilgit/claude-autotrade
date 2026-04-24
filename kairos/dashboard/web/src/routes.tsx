import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Home from "./pages/Home";
import RunNew from "./pages/RunNew";
import RunResults from "./pages/RunResults";
import Trades from "./pages/Trades";
import Compare from "./pages/Compare";
import GateReports from "./pages/GateReports";
import GateDetail from "./pages/GateDetail";

export default function AppRoutes() {
  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/"
          element={
            <Layout>
              <Home />
            </Layout>
          }
        />
        <Route
          path="/run/new"
          element={
            <Layout>
              <RunNew />
            </Layout>
          }
        />
        <Route
          path="/run/:id"
          element={
            <Layout>
              <RunResults />
            </Layout>
          }
        />
        <Route
          path="/trades/:id"
          element={
            <Layout>
              <Trades />
            </Layout>
          }
        />
        <Route
          path="/compare"
          element={
            <Layout>
              <Compare />
            </Layout>
          }
        />
        <Route
          path="/gate-reports"
          element={
            <Layout>
              <GateReports />
            </Layout>
          }
        />
        <Route
          path="/gate-reports/:name"
          element={
            <Layout>
              <GateDetail />
            </Layout>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
