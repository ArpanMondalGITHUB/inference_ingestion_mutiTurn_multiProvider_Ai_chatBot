import axios from "axios";
import { AXIOS_BASE_URL } from "../config/runtime"

const axiosInstance = axios.create(
    {
        baseURL:AXIOS_BASE_URL,
        headers:{
            "Content-Type":"application/json"
        },
        withCredentials: false,
    }
)


export default axiosInstance
