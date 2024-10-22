import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faHome, faReceipt, faCog } from '@fortawesome/free-solid-svg-icons';

const Sidebar = () => {
    const [activeIndex, setActiveIndex] = useState(0); // Track active item

    const items = [
        { label: 'Dashboard', icon: faHome, path:'/' },
        { label: 'Transactions', icon: faReceipt, path:'/transactions' },
        { label: 'Rules', icon: faCog, path:'/rules' },
    ];

    return (
        <div className="flex flex-col h-full w-64 bg-gray-800 text-white">
            {items.map((item, index) => (
                <Link 
                    key={index}
                    to={item.path}
                    onClick={() => setActiveIndex(index)}
                    className={`flex items-center p-4 text-left hover:bg-gray-700 transition-colors ${
                        activeIndex === index ? 'bg-gray-600' : ''
                    }`}
                >
                    <FontAwesomeIcon 
                        className={`mr-2 ${activeIndex === index ? 'text-blue-400' : 'text-gray-400'}`} 
                        icon={item.icon} 
                    />
                    <span className={`${activeIndex === index ? 'text-blue-400' : 'text-gray-400'}`}>
                        {item.label}
                    </span>
                </Link>
            ))}
        </div>
    );
};

export default Sidebar;
