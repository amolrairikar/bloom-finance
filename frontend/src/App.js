import React from 'react';
import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';
import Sidebar from './utils/Sidebar';
import ApiButton from './utils/RefreshTransactionsButton';
import Dashboard from './pages/Dashboard'
import Transactions from './pages/Transactions'
import Rules from './pages/Rules'

const App = () => {
    return (
      <Router>
        <div className="flex h-screen">
          <Sidebar />
          <div className="flex-1 relative">
              <ApiButton />
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/transactions" element={<Transactions />} />
                <Route path="/rules" element={<Rules />} />
              </Routes>
          </div>
        </div>
      </Router> 
    );
};

export default App;
