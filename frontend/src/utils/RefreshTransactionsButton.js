import React, { useState } from 'react';
import { useLocation } from 'react-router-dom';

const RefreshButton = () => {
    const [loading, setLoading] = useState(false);
    const [message, setMessage] = useState('');
    const [showPopup, setShowPopup] = useState(false); // State to control popup visibility
    const location = useLocation(); // Get the current location

    const refreshAPI = async () => {
        setLoading(true);
        setMessage('');
        setShowPopup(true); // Show the pop-up window when the button is clicked

        try {
            const response = await fetch('http://localhost:8000/transactions', {
                method: 'POST',
            });

            const result = await response.json();
            setMessage(result.message || 'API refreshed successfully!');

            // Hide the pop-up and message after 5 seconds
            setTimeout(() => {
                setShowPopup(false);
                setMessage('');
            }, 5000);
        } catch (error) {
            setMessage('Error refreshing API.');

            // Hide the pop-up and error message after 5 seconds
            setTimeout(() => {
                setShowPopup(false);
                setMessage('');
            }, 5000);
        }

        setLoading(false);
    };

    // Only render the button on the /transactions page
    if (location.pathname !== '/transactions') {
        return null;
    }

    return (
        <div className="relative flex flex-col items-center">
            <button
                onClick={refreshAPI}
                disabled={loading}
                className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-700 transition-colors"
            >
                {loading ? 'Refreshing...' : 'Refresh API'}
            </button>

            {/* Mini pop-up window */}
            {showPopup && (
                <div className="absolute top-16 w-64 p-4 bg-gray-900 text-white rounded shadow-lg z-50"> {/* Added z-50 for higher stacking order */}
                    {loading ? (
                        <div className="flex justify-center items-center">
                            <svg className="animate-spin h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                            </svg>
                            <span className="ml-2">Refreshing...</span>
                        </div>
                    ) : (
                        <p>{message}</p>
                    )}
                </div>
            )}
        </div>
    );
};

export default RefreshButton;
