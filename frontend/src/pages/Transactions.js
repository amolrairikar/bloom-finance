import React, { useState, useEffect, useRef } from 'react';
import { AgGridReact } from 'ag-grid-react';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';

const TransactionsPage = () => {
  const [rowData, setRowData] = useState([]);
  const gridRef = useRef(); // Reference to the AG Grid

  // Fetch the transactions from the API
  useEffect(() => {
    fetch('http://localhost:8000/transactions/')
      .then(response => response.json())
      .then(data => {
        setRowData(data);

        // Automatically resize columns once data is loaded
        setTimeout(() => {
          gridRef.current.api.sizeColumnsToFit();
        }, 100); // Slight delay to ensure grid is fully initialized
      })
      .catch(error => console.error('Error fetching transactions:', error));
  }, []);

  // Formatter for currency values
  const currencyFormatter = (params) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD'
    }).format(params.value);
  };

  // Define the columns for AG Grid
  const columnDefs = [
    { headerName: 'Merchant', field: 'merchant', sortable: true, filter: true },
    { headerName: 'Account', field: 'account_name', sortable: true, filter: true },
    { headerName: 'Category', field: 'category', sortable: true, filter: true },
    { 
      headerName: 'Amount', 
      field: 'amount', 
      sortable: true, 
      filter: true,
      valueFormatter: currencyFormatter // Use the currency formatter for the Amount column
    }
  ];

  return (
    <div className="p-5"> {/* Tailwind class for padding */}
      <div
        className="ag-theme-alpine"
        style={{ height: '500px', width: '100%' }} // Set a fixed height for the table
      >
        <AgGridReact
          ref={gridRef} // Assign grid reference
          rowData={rowData}
          columnDefs={columnDefs}
          onFirstDataRendered={() => gridRef.current.api.sizeColumnsToFit()} // Resize columns when grid is first rendered
          pagination={true}
          paginationPageSize={10}
          domLayout="normal" // Keep normal layout for fixed height and scroll
        />
      </div>
    </div>
  );
};

export default TransactionsPage;
